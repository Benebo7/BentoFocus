import email
from email.header import decode_header
import imaplib
import logging
from datetime import datetime, timedelta

from . import config

logger = logging.getLogger("agent.email")


def _decode_str(s: str | bytes) -> str:
    if isinstance(s, bytes):
        try:
            return s.decode("utf-8")
        except UnicodeDecodeError:
            return s.decode("latin-1", errors="ignore")
    return str(s)


def _clean_header(header_val: str | None) -> str:
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    parts = []
    for text, enc in decoded_parts:
        if isinstance(text, bytes):
            parts.append(text.decode(enc or "utf-8", errors="ignore"))
        else:
            parts.append(str(text))
    return "".join(parts).strip()


class EmailChecker:
    def __init__(self):
        self.host = config.IMAP_HOST
        self.port = config.IMAP_PORT
        self.user = config.IMAP_USER.strip()
        self.password = config.IMAP_PASSWORD.replace(" ", "").strip()

    def is_configured(self) -> bool:
        return bool(self.user and self.password)

    def get_summary(self, max_emails: int = 5) -> list[str]:
        """Busca e-mails não lidos das últimas 24h na caixa de entrada."""
        if not self.is_configured():
            return []

        summaries = []
        try:
            mail = imaplib.IMAP4_SSL(self.host, self.port)
            mail.login(self.user, self.password)
            mail.select("INBOX", readonly=True)

            # Busca mensagens não lidas
            since_date = (datetime.now() - timedelta(days=2)).strftime("%d-%b-%Y")
            search_query = f'(UNSEEN SINCE "{since_date}")'
            status, data = mail.search(None, search_query)

            if status == "OK" and data[0]:
                email_ids = data[0].split()
                # Pega os mais recentes
                recent_ids = email_ids[-max_emails:]
                recent_ids.reverse()

                for eid in recent_ids:
                    res, msg_data = mail.fetch(eid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                    if res == "OK":
                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        subject = _clean_header(msg.get("Subject"))
                        sender = _clean_header(msg.get("From"))
                        # Simplifica o remetente (tira e-mail longo)
                        sender_clean = sender.split("<")[0].strip().replace('"', '')
                        summaries.append(f"{sender_clean}: {subject}")

            mail.close()
            mail.logout()
        except Exception as e:
            logger.warning(f"Erro ao verificar e-mails via IMAP: {e}")

        return summaries

