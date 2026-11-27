import atexit
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

# Isolamento total antes de qualquer import do pacote bentofocus.
_BOOTSTRAP_DIR = tempfile.TemporaryDirectory(prefix="bentofocus-test-boot-")
atexit.register(_BOOTSTRAP_DIR.cleanup)
os.environ["BENTOFOCUS_DATA"] = _BOOTSTRAP_DIR.name
os.environ["BENTOFOCUS_DRY_RUN"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlmodel import create_engine  # noqa: E402

from bentofocus import config, db, stats  # noqa: E402
from bentofocus.focus import focus  # noqa: E402
from bentofocus.main import STATIC, app  # noqa: E402
from bentofocus.models import FocusSession, HabitLog, WindowSpan  # noqa: E402


async def _noop_collector() -> None:
    return None


class DailyExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="bentofocus-test-case-")
        tmp_path = Path(self._tmpdir.name)
        self._orig_data_dir = config.DATA_DIR
        self._orig_db_path = config.DB_PATH
        self._orig_engine = db.engine

        config.DATA_DIR = tmp_path
        config.DB_PATH = tmp_path / "bentofocus.db"
        db.engine = create_engine(
            f"sqlite:///{config.DB_PATH}",
            connect_args={"check_same_thread": False},
        )
        event.listen(db.engine, "connect", db._sqlite_pragmas)

        focus.current = None
        focus.plan = None
        stats._first_day = None
        stats._distract_cache.clear()
        db.init_db()

        self._collector_patch = patch("bentofocus.main.collector.run", new=_noop_collector)
        self._collector_patch.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._collector_patch.stop()
        focus.current = None
        focus.plan = None
        stats._first_day = None
        stats._distract_cache.clear()
        db.engine.dispose()
        config.DATA_DIR = self._orig_data_dir
        config.DB_PATH = self._orig_db_path
        db.engine = self._orig_engine
        self._tmpdir.cleanup()

    def test_environment_is_isolated(self) -> None:
        self.assertTrue(config.DRY_RUN)
        self.assertEqual(config.DATA_DIR, Path(self._tmpdir.name))
        self.assertNotIn(".local/share/bentofocus", str(config.DB_PATH))

    def test_empty_day_returns_valid_structure(self) -> None:
        resp = self.client.get("/api/export/daily", params={"day": "2026-11-20"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["day"], "2026-11-20")
        self.assertEqual(data["focus_minutes"], 0)
        self.assertEqual(data["blocks_count"], 0)
        self.assertEqual(data["sessions"], [])
        self.assertEqual(data["habits"], [])
        self.assertEqual(
            data["pc"],
            {"active": 0, "distraction": 0, "cheap": 0, "idle": 0, "total": 0},
        )
        self.assertEqual(data["top_distractions"], [])
        self.assertIsInstance(data["markdown"], str)
        self.assertIn("# BentoFocus — 2026-11-20", data["markdown"])
        self.assertIn("Nenhum bloco registrado neste dia.", data["markdown"])
        self.assertIn("Nenhum hábito registrado.", data["markdown"])
        self.assertIn("Nenhuma distração registrada.", data["markdown"])

    def test_default_day_uses_logical_day(self) -> None:
        resp = self.client.get("/api/export/daily")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["day"], db.logical_day().isoformat())

    def test_populated_day_sessions_habits_spans_and_markdown(self) -> None:
        target_day = date(2026, 11, 20)
        other_day = date(2026, 11, 19)

        with db.session() as s:
            s.add_all(
                [
                    FocusSession(
                        day=target_day,
                        started_at=datetime(2026, 11, 20, 9, 0, 0),
                        ended_at=datetime(2026, 11, 20, 9, 25, 0),
                        planned_min=25,
                        status="done",
                        intention="Estudar Kubernetes",
                        focus_s=1500.0,
                    ),
                    FocusSession(
                        day=target_day,
                        started_at=datetime(2026, 11, 20, 14, 0, 0),
                        ended_at=datetime(2026, 11, 20, 14, 45, 0),
                        planned_min=45,
                        status="manual",
                        intention="Ler documentação",
                        focus_s=2700.0,
                    ),
                    FocusSession(
                        day=target_day,
                        started_at=datetime(2026, 11, 20, 16, 0, 0),
                        ended_at=datetime(2026, 11, 20, 16, 0, 20),
                        planned_min=25,
                        status="cancelled",
                        intention="Interrompido rápido",
                        focus_s=20.0,
                    ),
                    FocusSession(
                        day=other_day,
                        started_at=datetime(2026, 11, 19, 10, 0, 0),
                        ended_at=datetime(2026, 11, 19, 10, 25, 0),
                        planned_min=25,
                        status="done",
                        intention="Outro dia",
                        focus_s=1500.0,
                    ),
                    HabitLog(day=target_day, habit="leitura"),
                    HabitLog(day=target_day, habit="academia"),
                    HabitLog(day=other_day, habit="faculdade"),
                    # 10 min dentro de target_day (clipping na fronteira de meia-noite)
                    WindowSpan(
                        start=datetime(2026, 11, 19, 23, 50, 0),
                        end=datetime(2026, 11, 20, 0, 10, 0),
                        wm_class="code",
                        title="editor.py",
                        category="active",
                        label="",
                    ),
                    # + 60 min active = 70 min active
                    WindowSpan(
                        start=datetime(2026, 11, 20, 9, 0, 0),
                        end=datetime(2026, 11, 20, 10, 0, 0),
                        wm_class="code",
                        title="main.py",
                        category="active",
                        label="",
                    ),
                    # 15 min YouTube vídeo longo (>= YOUTUBE_SHORT_S) -> distraction
                    WindowSpan(
                        start=datetime(2026, 11, 20, 12, 0, 0),
                        end=datetime(2026, 11, 20, 12, 15, 0),
                        wm_class="zen",
                        title="Kubernetes Deep Dive - YouTube",
                        category="distraction",
                        label="YouTube",
                    ),
                    # 10 min Instagram -> cheap
                    WindowSpan(
                        start=datetime(2026, 11, 20, 12, 20, 0),
                        end=datetime(2026, 11, 20, 12, 30, 0),
                        wm_class="zen",
                        title="Instagram",
                        category="distraction",
                        label="Instagram",
                    ),
                    # 60s YouTube curto (< YOUTUBE_SHORT_S) -> cheap ("YouTube Shorts / feed")
                    WindowSpan(
                        start=datetime(2026, 11, 20, 12, 35, 0),
                        end=datetime(2026, 11, 20, 12, 36, 0),
                        wm_class="zen",
                        title="Clip curto - YouTube",
                        category="distraction",
                        label="YouTube",
                    ),
                    # 20s Reddit -> cheap (< 1m, não entra em top_distractions)
                    WindowSpan(
                        start=datetime(2026, 11, 20, 12, 40, 0),
                        end=datetime(2026, 11, 20, 12, 40, 20),
                        wm_class="zen",
                        title="r/linux",
                        category="distraction",
                        label="Reddit",
                    ),
                    # 20 min idle
                    WindowSpan(
                        start=datetime(2026, 11, 20, 13, 0, 0),
                        end=datetime(2026, 11, 20, 13, 20, 0),
                        wm_class="",
                        title="",
                        category="idle",
                        label="",
                    ),
                ]
            )
            s.commit()

        resp = self.client.get("/api/export/daily", params={"day": "2026-11-20"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["day"], "2026-11-20")
        self.assertEqual(data["focus_minutes"], 70)
        self.assertEqual(data["blocks_count"], 2)
        self.assertEqual(data["habits"], ["academia", "leitura"])

        self.assertEqual(len(data["sessions"]), 3)
        expected_keys = {"started_at", "ended_at", "focus_min", "planned_min", "status", "intention"}
        for item in data["sessions"]:
            self.assertEqual(set(item.keys()), expected_keys)
        self.assertEqual(data["sessions"][0]["status"], "done")
        self.assertEqual(data["sessions"][1]["status"], "manual")
        self.assertEqual(data["sessions"][2]["status"], "cancelled")

        pc = data["pc"]
        self.assertEqual(pc["active"], 70)
        self.assertEqual(pc["distraction"], 15)
        self.assertEqual(pc["cheap"], 11)
        self.assertEqual(pc["idle"], 20)
        self.assertEqual(pc["total"], pc["active"] + pc["distraction"] + pc["cheap"] + pc["idle"])
        self.assertEqual(pc["total"], 116)

        self.assertEqual(
            data["top_distractions"],
            [
                {"label": "YouTube", "min": 15, "cheap": False},
                {"label": "Instagram", "min": 10, "cheap": True},
                {"label": "YouTube Shorts / feed", "min": 1, "cheap": True},
            ],
        )

        md = data["markdown"]
        self.assertIn("# BentoFocus — 2026-11-20 (sexta-feira)", md)
        self.assertIn("1h 10m em 2 blocos", md)
        self.assertIn("Estudar Kubernetes", md)
        self.assertIn("Ler documentação", md)
        self.assertIn("- [x] Academia", md)
        self.assertIn("- [x] Leitura", md)
        self.assertIn("**Total:** 1h 56m", md)
        self.assertIn("**Instagram**: 10m (barata)", md)
        self.assertIn("**YouTube**: 15m", md)

        md_resp = self.client.get(
            "/api/export/daily",
            params={"day": "2026-11-20", "format": "markdown"},
        )
        self.assertEqual(md_resp.status_code, 200)
        self.assertEqual(md_resp.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertEqual(
            md_resp.headers.get("content-disposition"),
            'attachment; filename="bentofocus-2026-11-20.md"',
        )
        self.assertEqual(md_resp.text, md)

    def test_populated_day_includes_running_session_elapsed_time(self) -> None:
        target_day = date(2026, 11, 20)
        fixed_now = datetime(2026, 11, 20, 10, 15, 0)

        with db.session() as s:
            done = FocusSession(
                day=target_day,
                started_at=datetime(2026, 11, 20, 9, 0, 0),
                ended_at=datetime(2026, 11, 20, 9, 25, 0),
                planned_min=25,
                status="done",
                intention="Bloco fechado",
                focus_s=1500.0,
            )
            running = FocusSession(
                day=target_day,
                started_at=datetime(2026, 11, 20, 10, 0, 0),
                ended_at=None,
                planned_min=25,
                status="running",
                intention="Bloco em curso",
                focus_s=0.0,
            )
            s.add_all([done, running])
            s.commit()
            s.refresh(running)
            s.expunge(running)

        focus.current = running
        with patch("bentofocus.focus.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            resp = self.client.get("/api/export/daily", params={"day": "2026-11-20"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["focus_minutes"], 40)
        self.assertEqual(data["blocks_count"], 2)
        self.assertEqual(len(data["sessions"]), 2)
        self.assertEqual(data["sessions"][1]["status"], "running")
        self.assertIsNone(data["sessions"][1]["ended_at"])
        self.assertEqual(data["sessions"][1]["focus_min"], 15.0)
        self.assertIn("em andamento", data["markdown"])

    def test_invalid_query_params_return_expected_errors(self) -> None:
        bad_day = self.client.get("/api/export/daily", params={"day": "not-a-date"})
        self.assertEqual(bad_day.status_code, 422)

        bad_date_value = self.client.get("/api/export/daily", params={"day": "2026-02-30"})
        self.assertEqual(bad_date_value.status_code, 422)

        bad_format = self.client.get("/api/export/daily", params={"format": "csv"})
        self.assertEqual(bad_format.status_code, 400)

    def test_frontend_has_daily_summary_controls_and_pctotal_includes_cheap(self) -> None:
        index_html = (STATIC / "index.html").read_text(encoding="utf-8")
        app_js = (STATIC / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="btn-resumo"', index_html)
        self.assertIn('id="modal-resumo"', index_html)
        self.assertIn("openSummary()", index_html)
        self.assertIn("summary-pre", index_html)
        self.assertIn("summary-copy", index_html)
        self.assertIn("summary-dl", index_html)
        self.assertIn("resumo", index_html)

        self.assertIn("/api/export/daily", app_js)
        self.assertIn("openSummary", app_js)

        start = app_js.find("pcTotal()")
        self.assertGreaterEqual(start, 0, "pcTotal() not found in app.js")
        brace_open = app_js.find("{", start)
        depth = 0
        brace_close = -1
        for idx in range(brace_open, len(app_js)):
            if app_js[idx] == "{":
                depth += 1
            elif app_js[idx] == "}":
                depth -= 1
                if depth == 0:
                    brace_close = idx
                    break
        self.assertGreater(brace_close, brace_open, "pcTotal() body not closed")
        body = app_js[brace_open:brace_close + 1]
        for key in ("p.active", "p.distraction", "p.cheap", "p.idle"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()
