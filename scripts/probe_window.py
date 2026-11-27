"""Mostra, a cada segundo, o que o coletor enxerga: janela ativa, classe,
inatividade e como ela seria classificada. Não grava nada nem bloqueia nada.

    uv run python scripts/probe_window.py [segundos]
"""

import asyncio
import sys

from bentofocus import config
from bentofocus.collector import x11
from bentofocus.collector.classifier import blocked_label, is_browser


async def main(seconds: int) -> None:
    print("ferramentas:", x11.available())
    for _ in range(seconds):
        win = await x11.active_window()
        idle = await x11.idle_seconds()
        label = blocked_label(win)
        if label:
            cat = f"BLOQUEÁVEL ({label}) → {'fecha aba' if is_browser(win) else 'minimiza'}"
        elif idle is not None and idle >= config.IDLE_THRESHOLD_S:
            cat = "inativo"
        else:
            cat = "ativo"
        idle_txt = "?" if idle is None else f"{idle:5.0f}s"
        if win:
            print(f"idle {idle_txt} | {win.cls or win.instance:<22.22} | {win.title[:60]:<60} | {cat}")
        else:
            print(f"idle {idle_txt} | (nenhuma janela ativa)")
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 15))
