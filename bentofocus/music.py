"""Spotify via MPRIS (D-Bus), o mesmo canal das teclas de mídia: abrir, tocar,
controlar e ler a música atual para o miniplayer."""

import asyncio
import json
import logging
import subprocess
import time

from . import config
from .collector import x11

log = logging.getLogger("bentofocus.music")

MPRIS = "org.mpris.MediaPlayer2.spotify"
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
ACTIONS = {"playpause": "PlayPause", "next": "Next", "previous": "Previous"}


async def _running() -> bool:
    out = await x11._run(
        "busctl", "--user", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "NameHasOwner", "s", MPRIS,
    )
    return bool(out and "true" in out)


# O Spotify trava quando recebe duas chamadas D-Bus ao mesmo tempo (medido:
# em paralelo, ~1 em 3 não responde). Então: uma conversa por vez (lock) e uma
# leitura só com tudo (GetAll), em vez de várias propriedades separadas.
_lock = asyncio.Lock()
_last: dict = {"running": False}
_last_at = 0.0
STALE_OK_S = 10  # leitura falhou: reaproveita a última boa por até 10 s


async def _call(method: str, *args: str) -> bool:
    async with _lock:
        return await x11._run("busctl", "--user", "call", MPRIS, PATH, PLAYER, method, *args) is not None


async def state() -> dict:
    global _last, _last_at
    if not await _running():
        _last = {"running": False}
        return _last
    async with _lock:
        out = await x11._run("busctl", "--user", "--json=short", "call", MPRIS, PATH,
                             "org.freedesktop.DBus.Properties", "GetAll", "s", PLAYER)
    try:
        props = json.loads(out)["data"][0]
    except (TypeError, ValueError, KeyError, IndexError):
        props = None
    if not props:
        if _last.get("running") and time.monotonic() - _last_at < STALE_OK_S:
            return _last
        return {"running": True, "playing": False, "title": "", "artist": "", "art": ""}

    meta = (props.get("Metadata") or {}).get("data") or {}

    def get(key: str):
        return (meta.get(key) or {}).get("data")

    _last = {
        "running": True,
        "playing": (props.get("PlaybackStatus") or {}).get("data") == "Playing",
        "title": get("xesam:title") or "",
        "artist": ", ".join(get("xesam:artist") or []),
        "art": get("mpris:artUrl") or "",
        "position_s": ((props.get("Position") or {}).get("data") or 0) / 1e6,
        "length_s": (get("mpris:length") or 0) / 1e6,
        "volume": (props.get("Volume") or {}).get("data"),  # 0..1, None se o player não informar
    }
    _last_at = time.monotonic()
    return _last


async def control(action: str) -> None:
    await _call(ACTIONS[action])


async def set_volume(value: float) -> None:
    value = min(1.0, max(0.0, value))
    async with _lock:
        await x11._run("busctl", "--user", "set-property", MPRIS, PATH, PLAYER, "Volume", "d", f"{value:.3f}")
    if _last.get("running"):
        _last["volume"] = value  # a próxima leitura já mostra o valor novo


async def audio_snapshot() -> str:
    """Retrato do áudio para diagnóstico: saídas Bluetooth (perfil, codec,
    estado) e para qual saída o som do Spotify está indo de fato. Vai para o
    log no início dos blocos; quando a música "não sai", é aqui que se vê se o
    fone estava no modo ligação (HFP), com a saída em erro ou com o Spotify
    preso em outra saída."""
    out = await x11._run("pw-dump", timeout=5)
    try:
        objs = json.loads(out) if out else []
    except ValueError:
        return "pw-dump ilegível"
    nodes = {o["id"]: o for o in objs if o.get("type", "").endswith(":Node")}
    props = lambda o: (o.get("info") or {}).get("props") or {}
    parts = []
    for o in nodes.values():
        p = props(o)
        if str(p.get("node.name", "")).startswith("bluez_output"):
            parts.append(f"fone[{p.get('api.bluez5.profile')}/{p.get('api.bluez5.codec')}]={(o.get('info') or {}).get('state')}")
    targets = set()
    spotify_ids = {i for i, o in nodes.items() if props(o).get("application.name") == "Spotify"
                   and "Stream" in str(props(o).get("media.class", ""))}
    for o in objs:
        if o.get("type", "").endswith(":Link"):
            info = o.get("info") or {}
            if info.get("output-node-id") in spotify_ids:
                dst = nodes.get(info.get("input-node-id"))
                targets.add(props(dst).get("node.name", "?") if dst else "?")
    states = [str((nodes[i].get("info") or {}).get("state")) for i in spotify_ids]
    parts.append(f"spotify[{','.join(states) or 'sem stream'}]→{','.join(sorted(targets)) or 'nenhuma saída'}")
    return " | ".join(parts) or "sem saídas de áudio"


async def start(play: bool = True) -> None:
    """Abre o Spotify se estiver fechado (e deixa aberto) e, com `play`, toca:
    a playlist de foco do config, se houver, ou continua de onde parou."""
    if config.DRY_RUN:
        return
    if not await _running():
        try:
            # O flatpak põe o Spotify num escopo próprio do systemd: ele não
            # morre junto se o serviço do BentoFocus reiniciar.
            subprocess.Popen(config.SPOTIFY_CMD, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            log.warning("não consegui abrir o Spotify: %s", config.SPOTIFY_CMD)
            return
        for _ in range(50):  # até ~25s para subir
            await asyncio.sleep(0.5)
            if await _running():
                break
        else:
            return
        await asyncio.sleep(2)  # o MPRIS aparece um pouco antes de o player aceitar comandos
    if not play:
        return
    for _ in range(3):
        ok = await (_call("OpenUri", "s", config.SPOTIFY_FOCUS_URI) if config.SPOTIFY_FOCUS_URI else _call("Play"))
        if ok:
            break
        await asyncio.sleep(1.5)
    await asyncio.sleep(4)  # dá tempo de o som começar a sair
    log.info("áudio no início do bloco: %s", await audio_snapshot())
