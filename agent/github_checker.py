import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Any

from . import config

logger = logging.getLogger("agent.github")


class GitHubChecker:
    def __init__(self):
        self.token = config.GITHUB_TOKEN
        self.username = config.GITHUB_USERNAME
        self.target_repo = config.GITHUB_TARGET_REPO

    def is_configured(self) -> bool:
        return bool(self.token and self.target_repo)

    def _fetch(self, url: str) -> Any:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "BentoFocus-Agent",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_summary(self) -> dict[str, list[str]]:
        """Retorna resumo focado estritamente no repositório alvo: novos PRs para revisar e novas issues."""
        if not self.is_configured():
            return {"repo_prs": [], "repo_issues": []}

        repo_prs = []
        repo_issues = []
        now = datetime.now(timezone.utc)

        try:
            # 1. PRs abertos recentemente no repo alvo (ex: owner/repo) ordenados por criação
            pulls_url = f"https://api.github.com/repos/{self.target_repo}/pulls?state=open&sort=created&direction=desc&per_page=10"
            pulls = self._fetch(pulls_url)
            for pr in pulls:
                author = pr.get("user", {}).get("login", "")
                # Ignora PRs do próprio usuário e bots
                if author.lower() == self.username.lower() or author.endswith("[bot]"):
                    continue
                created_dt = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                days_ago = (now - created_dt).days
                age_str = "hoje" if days_ago == 0 else ("ontem" if days_ago == 1 else f"há {days_ago} dias")
                draft_str = " [Draft]" if pr.get("draft") else ""
                repo_prs.append(f"PR #{pr.get('number')} por @{author}{draft_str}: \"{pr.get('title')}\" ({age_str})")
                if len(repo_prs) >= 4:
                    break
        except Exception as e:
            logger.warning(f"Erro ao buscar novos PRs de {self.target_repo}: {e}")

        try:
            # 2. Issues abertas recentemente no repo alvo
            issues_url = f"https://api.github.com/repos/{self.target_repo}/issues?state=open&sort=created&direction=desc&per_page=10"
            issues = self._fetch(issues_url)
            for iss in issues:
                if "pull_request" in iss:
                    continue
                author = iss.get("user", {}).get("login", "")
                if author.lower() == self.username.lower():
                    continue
                created_dt = datetime.fromisoformat(iss["created_at"].replace("Z", "+00:00"))
                days_ago = (now - created_dt).days
                age_str = "hoje" if days_ago == 0 else ("ontem" if days_ago == 1 else f"há {days_ago} dias")
                labels = ", ".join([l["name"] for l in iss.get("labels", []) if l.get("name")])
                lbl_str = f" [{labels}]" if labels else ""
                repo_issues.append(f"Issue #{iss.get('number')} por @{author}{lbl_str}: \"{iss.get('title')}\" ({age_str})")
                if len(repo_issues) >= 3:
                    break
        except Exception as e:
            logger.warning(f"Erro ao buscar novas issues de {self.target_repo}: {e}")

        return {"repo_prs": repo_prs, "repo_issues": repo_issues}
