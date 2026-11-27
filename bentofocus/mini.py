"""Abre o miniplayer (window.py --mini) como processo solto. Se ele já estiver
aberto, o GTK só traz a janela existente: abrir de novo é seguro."""

import logging
import subprocess
from pathlib import Path

from . import config

log = logging.getLogger("bentofocus.mini")
WINDOW = Path(__file__).parent / "window.py"


def open() -> None:
    if config.DRY_RUN:
        return
    try:
        # systemd-run --scope: a janela vai para um escopo próprio e não é
        # derrubada junto quando o serviço reinicia.
        subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", "--collect",
                          "/usr/bin/python3", str(WINDOW), "--mini"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        log.warning("não consegui abrir o miniplayer")
