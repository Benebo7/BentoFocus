"""Loop do coletor: amostra a janela ativa, grava intervalos para o painel
"no PC hoje" e, durante um bloco, bloqueia o que estiver em BLOCK_RULES."""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from .. import config
from ..db import session
from ..focus import focus
from ..models import FocusSession, WindowSpan
from . import x11
from .classifier import blocked_label, is_browser

log = logging.getLogger("bentofocus.collector")

GAP_S = 15  # intervalo maior que isso entre amostras = suspensão/hibernação
SPAN_FLUSH_S = 30
ACTION_COOLDOWN_S = 2  # o título demora a atualizar depois do Ctrl+W
NUDGE_COOLDOWN_S = 15


class Collector:
    def __init__(self) -> None:
        self.status: dict = {"running": False}
        self._span: WindowSpan | None = None
        self._span_key: tuple | None = None
        self._last_tick: datetime | None = None
        self._last_span_flush = 0.0
        self._last_action: dict[int, float] = {}
        self._last_nudge = 0.0

    async def run(self) -> None:
        tools = x11.available()
        if not (tools["xdotool"] and tools["xprop"]):
            log.warning("xdotool/xprop ausentes: sem rastreio de janela nem bloqueio. %s", tools)
        self.status = {"running": True, "tools": tools}
        x11.input_watcher.start(await x11.system_idle_seconds())
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    log.exception("falha numa amostra do coletor")
                await asyncio.sleep(config.TICK_FOCUS if focus.current else config.TICK_IDLE)
        finally:
            await x11.input_watcher.stop()
            self._close_span(datetime.now())

    async def tick(self) -> None:
        now = datetime.now()
        if self._last_tick and (now - self._last_tick).total_seconds() > GAP_S:
            self._close_span(None)  # voltou de suspensão: o intervalo termina onde parou de ser visto
        self._last_tick = now

        win = await x11.active_window()
        idle = await x11.idle_seconds()
        s = focus.current

        label = blocked_label(win)
        youtube_ok = label == "YouTube" and s is not None and s.allow_youtube
        idle_now = idle is not None and idle >= config.IDLE_THRESHOLD_S
        if idle_now and label:
            # Vídeo não gera input: sem mouse/teclado mas com mídia tocando no
            # navegador = assistindo. Pausado (ou app parado) = você saiu.
            away = not await x11.browser_media_playing()
        else:
            away = idle_now
        if away:
            category = "idle"
        elif label and not youtube_ok:
            category = "distraction"
        else:
            category = "active"  # inclui aula liberada no bloco

        self._record_span(now, win, category, label if category == "distraction" else "", s.id if s else None, idle)

        if s is not None:
            if category == "distraction":
                await self._block(s, win, label)
            if now >= focus.ends_at():
                await focus.stop(cancelled=False, notify=True)

        self.status.update(
            window=win.title if win else None,
            wm_class=win.cls if win else None,
            category=category,
            label=label,
            idle_s=idle,
            idle_supported=idle is not None,
            idle_source="xinput" if x11.input_watcher.alive else "sistema",
            at=now.isoformat(timespec="seconds"),
        )

    async def _block(self, s: FocusSession, win: x11.Window | None, label: str | None) -> None:
        if win is None or config.DRY_RUN:
            return
        t = time.monotonic()
        if t - self._last_action.get(win.id, 0) < ACTION_COOLDOWN_S:
            return
        self._last_action[win.id] = t
        # Navegador: fecha a aba. App: minimiza (sem matar o processo, para
        # não perder nada); se você voltar nele, é minimizado de novo.
        await (x11.close_tab(win) if is_browser(win) else x11.minimize(win))
        if t - self._last_nudge > NUDGE_COOLDOWN_S:
            self._last_nudge = t
            body = f"Foco em: {s.intention}" if s.intention else "Volta pro bloco."
            await x11.notify(f"{label} bloqueado até o fim do bloco", body)

    # --- intervalos de janela -----------------------------------------------

    def _record_span(
        self, now: datetime, win: x11.Window | None, category: str, label: str, sid: int | None, idle: float | None
    ) -> None:
        if category == "idle":
            key = ("idle", sid)
        else:
            key = (category, label, win.cls if win else "", win.title if win else "", sid)

        if self._span is not None and key == self._span_key:
            self._span.end = now
            if time.monotonic() - self._last_span_flush > SPAN_FLUSH_S:
                self._save(self._span)
            return

        start = now
        if category == "idle" and idle:  # a inatividade começou antes de ser detectada
            start = now - timedelta(seconds=idle)
            if self._span is not None:
                start = max(start, self._span.start)
        self._close_span(start)
        span = WindowSpan(
            start=start,
            end=now,
            wm_class=(win.cls or win.instance) if win and category != "idle" else "",
            title=(win.title[:300] if win and category != "idle" else ""),
            category=category,
            label=label,
            session_id=sid,
        )
        with session() as db:
            db.add(span)
            db.commit()
            db.refresh(span)
            db.expunge(span)
        self._span, self._span_key = span, key
        self._last_span_flush = time.monotonic()

    def _close_span(self, now: datetime | None) -> None:
        if self._span is None:
            return
        if now is not None:
            self._span.end = now
        self._save(self._span)
        self._span = self._span_key = None

    def _save(self, span: WindowSpan) -> None:
        with session() as db:
            db.merge(span)
            db.commit()
        self._last_span_flush = time.monotonic()


collector = Collector()
