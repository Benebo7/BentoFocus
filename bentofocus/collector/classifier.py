from .. import config
from .x11 import Window


def blocked_label(win: Window | None) -> str | None:
    """Nome da regra de bloqueio que casa com a janela, ou None."""
    if win is None:
        return None
    for label, field, pattern in config.BLOCK_RULES:
        targets = (win.title,) if field == "title" else (win.instance, win.cls)
        if any(pattern.search(t) for t in targets if t):
            return label
    return None


def is_browser(win: Window) -> bool:
    return bool(config.BROWSER_CLASSES.match(win.cls) or config.BROWSER_CLASSES.match(win.instance))

