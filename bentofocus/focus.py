"""Ciclo de vida dos blocos de foco. Tudo roda no event loop (endpoints são
async), então coletor e API nunca mexem na sessão ao mesmo tempo.

Uma sequência ("plano") é N blocos iguais com pausa entre eles: ao fim de um
bloco, a pausa começa e o próximo abre sozinho. Parar um bloco no meio
encerra a sequência.
"""

import asyncio
import json
import math
from datetime import datetime, timedelta

from . import config, mini, music
from .collector import x11
from .db import logical_day, session
from .models import KV, FocusSession


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


class FocusManager:
    def __init__(self) -> None:
        self.current: FocusSession | None = None
        self.plan: dict | None = None  # {intention, minutes, allow_youtube, spotify, total, index, next_at}
        self._timer: asyncio.Task | None = None

    # --- consultas ----------------------------------------------------------

    def ends_at(self) -> datetime | None:
        if self.current is None:
            return None
        return self.current.started_at + timedelta(minutes=self.current.planned_min)

    def elapsed_s(self, s: FocusSession, until: datetime | None = None) -> float:
        """Foco efetivo = tempo de bloco decorrido, limitado ao planejado."""
        until = until or datetime.now()
        return max(0.0, min((until - s.started_at).total_seconds(), s.planned_min * 60))

    # --- ações --------------------------------------------------------------

    async def start(
        self, intention: str, minutes: int, allow_youtube: bool, cycles: int = 1, spotify: bool = False
    ) -> FocusSession:
        if self.current is not None:
            raise ValueError("Já existe um bloco em andamento.")
        self._cancel_timer()
        plan = None
        if cycles > 1:
            plan = dict(intention=intention.strip(), minutes=minutes, allow_youtube=allow_youtube,
                        spotify=spotify, total=cycles, index=1, next_at=None)
        self._save_plan(plan)
        return await self._open(intention, minutes, allow_youtube, spotify)

    async def extend(self, minutes: int) -> None:
        """+N minutos de FOCO. Com bloco rodando, estica o bloco. Na pausa logo
        depois de um bloco (você nem viu o timer acabar), retoma esse mesmo
        bloco até agora + N: o tempo que você seguiu trabalhando na pausa conta
        como foco, e numa sequência a pausa e o próximo bloco ficam para depois."""
        if self.current is not None:
            self.current.planned_min += minutes
            self.flush()
            return

        with session() as db:
            last = (db.query(FocusSession).filter(FocusSession.status.in_(("done", "cancelled")))
                    .order_by(FocusSession.ended_at.desc()).first())
            if last:
                db.expunge(last)
        if last is None or last.ended_at is None or last.status != "done":
            raise ValueError("Nenhum bloco concluído para retomar.")
        p = self.plan
        break_end = (datetime.fromisoformat(p["next_at"]) if p and p.get("next_at")
                     else last.ended_at + timedelta(minutes=config.break_minutes(last.planned_min)))
        now = _now()
        if now >= break_end:
            raise ValueError("A pausa desse bloco já acabou.")

        self._cancel_timer()  # nada de "próximo bloco" nem "pausa acabou" no meio
        if p:
            p["next_at"] = None
            self._save_plan(p)
        last.status = "running"
        last.ended_at = None
        # Termina em agora + N (arredondado para cima ao minuto, o campo é inteiro).
        last.planned_min = math.ceil((now - last.started_at).total_seconds() / 60) + minutes
        self.current = last
        self.flush()
        await self._silence_notifications()

    async def log(self, minutes: int, intention: str, ended_at: datetime | None = None) -> FocusSession:
        """Foco feito sem o timer (esqueceu de começar o bloco). Entra como bloco
        "manual": conta no dia, nos gráficos e no histórico, mas não abre pausa
        nem bloqueia nada. Não pode cair por cima de outro bloco (contaria duas vezes)."""
        end = (ended_at or _now()).replace(microsecond=0)
        if end > _now() + timedelta(minutes=1):
            raise ValueError("Esse horário ainda não chegou.")
        start = end - timedelta(minutes=minutes)
        with session() as db:
            near = db.query(FocusSession).filter(
                FocusSession.started_at < end, FocusSession.started_at > start - timedelta(days=1)).all()
            for o in near:
                o_end = o.ended_at or datetime.now()  # bloco rodando: o que já foi feito
                if o_end > start:
                    raise ValueError(f"Já tem foco das {o.started_at:%H:%M} às {o_end:%H:%M} nesse intervalo.")
            s = FocusSession(day=logical_day(start), started_at=start, planned_min=minutes, ended_at=end,
                             status="manual", intention=intention.strip(), focus_s=minutes * 60)
            db.add(s)
            db.commit()
            db.refresh(s)
            db.expunge(s)
        return s

    def delete_manual(self, session_id: int) -> None:
        """Apaga um foco registrado à mão (errou o tempo). Blocos com timer não."""
        with session() as db:
            s = db.get(FocusSession, session_id)
            if s is None or s.status != "manual":
                raise ValueError("Só dá para apagar foco registrado à mão.")
            db.delete(s)
            db.commit()

    async def stop(self, cancelled: bool, notify: bool = False, ended_at: datetime | None = None) -> FocusSession | None:
        s = self.current
        if s is None:
            return None
        s.ended_at = ended_at or _now()
        s.focus_s = self.elapsed_s(s, s.ended_at)
        s.status = "cancelled" if cancelled else "done"
        self.flush()
        self.current = None
        await self._restore_notifications()

        brk = config.break_minutes(s.planned_min)
        p = self.plan
        if cancelled:
            self._save_plan(None)  # parar no meio encerra a sequência
            return s

        if p and p["index"] < p["total"]:
            next_at = s.ended_at + timedelta(minutes=brk)
            p["next_at"] = next_at.isoformat()
            self._save_plan(p)
            self._timer = asyncio.create_task(self._auto_next(next_at))
            if notify:
                await x11.notify(
                    f"Bloco {p['index']} de {p['total']} concluído",
                    f"Pausa de {brk} min, bloqueio liberado. O próximo começa às {next_at:%H:%M}, sozinho.",
                )
                await x11.play_sound("complete")
        else:
            finished_plan = p is not None
            self._save_plan(None)
            if notify:
                title = f"Sequência concluída: {p['total']} blocos" if finished_plan else "Bloco concluído"
                await x11.notify(title, f"Pausa de {brk} min, bloqueio liberado.")
                await x11.play_sound("complete")
                self._timer = asyncio.create_task(self._break_over(brk))
        return s

    async def skip_break(self) -> None:
        """Na pausa de uma sequência: começa o próximo bloco agora."""
        if self.current is not None or not (self.plan and self.plan.get("next_at")):
            raise ValueError("Nenhuma pausa de sequência em andamento.")
        self._cancel_timer()
        await self._start_next()

    async def end_plan(self) -> None:
        self._cancel_timer()
        self._save_plan(None)

    def flush(self) -> None:
        if self.current is None:
            return
        with session() as db:
            db.merge(self.current)
            db.commit()

    async def recover(self) -> None:
        """Ao subir o serviço: retoma um bloco interrompido (reboot, crash),
        fecha o que já deveria ter terminado e retoma a pausa de uma sequência."""
        with session() as db:
            kv = db.get(KV, "plan")
            self.plan = json.loads(kv.value) if kv else None
            running = db.query(FocusSession).filter(FocusSession.status == "running").all()
            for s in running:
                db.expunge(s)
        for s in running:
            self.current = s
            if datetime.now() >= self.ends_at():
                await self.stop(cancelled=False, ended_at=self.ends_at())
        if self.current is not None:
            return
        await self._restore_notifications()

        p = self.plan
        if p and p.get("next_at"):
            next_at = datetime.fromisoformat(p["next_at"])
            if datetime.now() - next_at > timedelta(minutes=config.PLAN_STALE_MIN):
                await self.end_plan()  # PC ficou desligado: não abre bloco do nada
            elif self._timer is None or self._timer.done():
                self._timer = asyncio.create_task(self._auto_next(next_at))
        elif p:
            self._save_plan(None)  # plano sem bloco nem pausa: resto inconsistente

    # --- internos -------------------------------------------------------------

    async def _open(self, intention: str, minutes: int, allow_youtube: bool, spotify: bool = False) -> FocusSession:
        now = _now()
        s = FocusSession(
            day=logical_day(now),
            started_at=now,
            planned_min=minutes,
            intention=intention.strip(),
            allow_youtube=allow_youtube,
        )
        with session() as db:
            db.add(s)
            db.commit()
            db.refresh(s)
            db.expunge(s)
        self.current = s
        await self._silence_notifications()
        if spotify:
            asyncio.create_task(music.start())  # em segundo plano: abrir o Spotify leva segundos
        if config.MINI_ON_FOCUS:
            mini.open()
        return s

    async def _start_next(self) -> None:
        p = self.plan
        p["index"] += 1
        p["next_at"] = None
        self._save_plan(p)
        # Avisa antes de abrir: o bloco silencia as notificações do GNOME.
        await x11.notify(f"Bloco {p['index']} de {p['total']} começou", p["intention"] or "Bora.")
        await x11.play_sound("bell")
        await self._open(p["intention"], p["minutes"], p["allow_youtube"], p.get("spotify", False))

    async def _auto_next(self, at: datetime) -> None:
        await asyncio.sleep(max(0.0, (at - datetime.now()).total_seconds()))
        if self.current is None and self.plan and self.plan.get("next_at"):
            self._timer = None
            await self._start_next()

    async def _break_over(self, minutes: int) -> None:
        await asyncio.sleep(minutes * 60)
        if self.current is None:
            await x11.notify("Pausa acabou", "Bora pro próximo bloco?")
            await x11.play_sound("bell")

    def _cancel_timer(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    def _save_plan(self, plan: dict | None) -> None:
        self.plan = plan
        with session() as db:
            kv = db.get(KV, "plan")
            if plan is None:
                if kv:
                    db.delete(kv)
            elif kv:
                kv.value = json.dumps(plan)
            else:
                db.add(KV(key="plan", value=json.dumps(plan)))
            db.commit()

    async def _silence_notifications(self) -> None:
        if config.DRY_RUN:
            return
        prev = await x11.get_banners()
        if prev is None:
            return
        with session() as db:
            if db.get(KV, "banners_prev") is None:  # não sobrescreve num bloco retomado
                db.add(KV(key="banners_prev", value=str(prev)))
                db.commit()
        await x11.set_banners(False)

    async def _restore_notifications(self) -> None:
        with session() as db:
            kv = db.get(KV, "banners_prev")
            if kv is None:
                return
            prev = kv.value == "True"
            db.delete(kv)
            db.commit()
        if not config.DRY_RUN:
            await x11.set_banners(prev)


focus = FocusManager()
