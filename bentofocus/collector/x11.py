"""Tudo que conversa com o desktop: janela ativa, inatividade, bloqueio,
notificações. Cada chamada é um subprocesso assíncrono e falha de forma
segura (retorna None/False) se a ferramenta não existir."""

import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass

SOUND_DIR = "/usr/share/sounds/freedesktop/stereo"


@dataclass(frozen=True)
class Window:
    id: int
    instance: str  # 1º valor do WM_CLASS (ex.: "app.zen_browser.zen")
    cls: str  # 2º valor do WM_CLASS (ex.: "zen")
    title: str


def available() -> dict[str, bool]:
    return {tool: shutil.which(tool) is not None for tool in ("xdotool", "xprop", "gdbus", "notify-send")}


async def _run(*args: str, timeout: float = 2.0) -> str | None:
    if not shutil.which(args[0]):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    return out.decode("utf-8", errors="replace")


_PROP_RE = re.compile(r'^(\w+)\([^)]*\) = (.*)$')
_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


async def active_window() -> Window | None:
    out = await _run("xdotool", "getactivewindow")
    if not out or not out.strip().isdigit():
        return None
    wid = int(out.strip())
    props = await _run("xprop", "-id", str(wid), "WM_CLASS", "_NET_WM_NAME", "WM_NAME")
    if props is None:
        return None
    values: dict[str, list[str]] = {}
    for line in props.splitlines():
        m = _PROP_RE.match(line.strip())
        if m:
            values[m.group(1)] = [_unescape(s) for s in _STR_RE.findall(m.group(2))]
    wm_class = values.get("WM_CLASS", [])
    title = (values.get("_NET_WM_NAME") or values.get("WM_NAME") or [""])[0]
    return Window(
        id=wid,
        instance=wm_class[0] if wm_class else "",
        cls=wm_class[1] if len(wm_class) > 1 else "",
        title=title,
    )


class InputWatcher:
    """Hora do último input físico (teclado/mouse), via eventos "crus" do
    XInput2 (`xinput test-xi2 --root`).

    Por que não basta o contador de inatividade do sistema: jogos (SDL, Proton)
    chamam XResetScreenSaver a cada ~30 s para a tela não apagar, e no X11 isso
    zera esse contador sem ninguém tocar em nada; 40 min parado num jogo viravam
    "jogando". Eventos crus só existem com input de verdade.

    Não interpreta a saída: qualquer byte lido = houve input. Lê em blocos, então
    o custo não cresce com a taxa de eventos do mouse.
    """

    def __init__(self) -> None:
        self.last_input: float | None = None  # time.monotonic() do último input
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and self.last_input is not None

    def start(self, initial_idle: float | None) -> None:
        if self._task is None and shutil.which("xinput"):
            if initial_idle is not None:
                self.last_input = time.monotonic() - initial_idle
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.kill()

    async def _run(self) -> None:
        cmd = ["xinput", "test-xi2", "--root"]
        if shutil.which("stdbuf"):
            cmd = ["stdbuf", "-o0", *cmd]  # sem buffer: um toque de tecla aparece na hora
        while True:
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                started = time.monotonic()
                while chunk := await self._proc.stdout.read(65536):
                    if time.monotonic() - started > 0.5:  # ignora o que for impresso ao abrir
                        self.last_input = time.monotonic()
                await self._proc.wait()
            except OSError:
                pass
            await asyncio.sleep(5)  # xinput caiu (X reiniciou?): tenta de novo


input_watcher = InputWatcher()


async def system_idle_seconds() -> float | None:
    """Contador de inatividade do sistema: IdleMonitor do GNOME (Mutter) ou
    xprintidle. Zerado por jogos (ver InputWatcher); serve de reserva."""
    out = await _run(
        "gdbus", "call", "--session",
        "--dest", "org.gnome.Mutter.IdleMonitor",
        "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
        "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime",
    )
    # A resposta é "(uint64 90339,)": casar o número depois do tipo, senão o
    # "64" de "uint64" vira o tempo ocioso (bug que zerava a inatividade).
    if out and (m := re.search(r"uint64\s+(\d+)", out)):
        return int(m.group(1)) / 1000
    out = await _run("xprintidle")
    if out and out.strip().isdigit():
        return int(out.strip()) / 1000
    return None


async def idle_seconds() -> float | None:
    """Tempo sem mouse/teclado de verdade. Com o InputWatcher rodando, vem dos
    eventos crus (imune a jogos); senão, do contador do sistema."""
    if input_watcher.alive:
        return time.monotonic() - input_watcher.last_input
    return await system_idle_seconds()


async def browser_media_playing() -> bool:
    """Algum navegador está tocando mídia agora? Firefox/Zen/Chrome anunciam
    vídeo e áudio via MPRIS (o mesmo canal das teclas de mídia). O Spotify
    fica de fora: música tocando com você longe não é atividade."""
    out = await _run("busctl", "--user", "--json=short", "call", "org.freedesktop.DBus",
                     "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames")
    try:
        names = json.loads(out)["data"][0] if out else []
    except (ValueError, KeyError, IndexError):
        return False
    players = [n for n in names if n.startswith("org.mpris.MediaPlayer2.") and "spotify" not in n]
    for name in players:
        st = await _run("busctl", "--user", "--json=short", "get-property", name,
                        "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "PlaybackStatus")
        if st and '"Playing"' in st:
            return True
    return False


async def close_tab(win: Window) -> bool:
    """Ctrl+W na aba ativa, mas só se essa janela ainda estiver em foco:
    o atalho vai para a janela focada, então conferir antes evita fechar a
    aba errada se você trocou de janela no meio do caminho."""
    current = await active_window()
    if current is None or current.id != win.id:
        return False
    return await _run("xdotool", "key", "--clearmodifiers", "ctrl+w") is not None


async def minimize(win: Window) -> bool:
    return await _run("xdotool", "windowminimize", str(win.id)) is not None


async def notify(title: str, body: str = "", urgent: bool = False) -> None:
    args = ["notify-send", "-a", "BentoFocus", "-i", "alarm-symbolic"]
    if urgent:
        args += ["-u", "critical"]
    await _run(*args, title, body)


async def play_sound(name: str = "complete") -> None:
    path = f"{SOUND_DIR}/{name}.oga"
    if await _run("pw-play", path, timeout=10) is None:
        await _run("canberra-gtk-play", "-i", name, timeout=10)


async def get_banners() -> bool | None:
    out = await _run("gsettings", "get", "org.gnome.desktop.notifications", "show-banners")
    return None if out is None else out.strip() == "true"


async def set_banners(enabled: bool) -> None:
    await _run("gsettings", "set", "org.gnome.desktop.notifications", "show-banners", str(enabled).lower())
