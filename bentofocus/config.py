"""Configuração central. Edite à vontade: tudo aqui é lido na inicialização."""

import os
import re
from pathlib import Path

PORT = int(os.environ.get("BENTOFOCUS_PORT", "7777"))

DATA_DIR = Path(os.environ.get("BENTOFOCUS_DATA", Path.home() / ".local/share/bentofocus"))
DB_PATH = DATA_DIR / "bentofocus.db"

# Modo de teste: coleta e calcula tudo, mas não fecha abas, não minimiza jogos
# e não mexe nas notificações do GNOME.
DRY_RUN = os.environ.get("BENTOFOCUS_DRY_RUN") == "1"

# O "dia" vira à meia-noite (0h), acompanhando a data do calendário e do agente.
DAY_START_HOUR = 0

# Intervalo de amostragem da janela ativa (segundos).
TICK_IDLE = 3.0  # fora de um bloco: só observa
TICK_FOCUS = 1.0  # dentro de um bloco: bloqueia rápido

# Sem mouse/teclado por mais que isso = inativo (só no painel "no PC hoje";
# dentro de um bloco o tempo conta inteiro, porque parado = caderno).
IDLE_THRESHOLD_S = 180

FOCUS_DURATIONS = (25, 35, 45, 60)
CYCLE_OPTIONS = (1, 2, 3, 4)  # blocos em sequência; os seguintes começam sozinhos


def break_minutes(block_min: int) -> int:
    """Pausa proporcional ao bloco: 25 → 5, 35 → 10, 45 ou mais → 15."""
    return 15 if block_min >= 45 else 10 if block_min >= 35 else 5


# Spotify ao começar um bloco (liga/desliga pelo botão no painel).
SPOTIFY_CMD = ["flatpak", "run", "com.spotify.Client"]
# O que tocar ao começar um bloco. None = continua de onde parou no Spotify.
# Para uma playlist fixa: no Spotify, Compartilhar → Copiar link, e troque
# "https://open.spotify.com/playlist/ID?..." por "spotify:playlist:ID".
SPOTIFY_FOCUS_URI: str | None = None

# Miniplayer: janelinha sempre por cima com o timer. Abre sozinha no início
# de cada bloco e fecha quando não há bloco nem pausa.
MINI_ON_FOCUS = True

# Se o PC ficou desligado e a pausa de uma sequência já passou disso, a
# sequência é encerrada em vez de abrir um bloco do nada quando você voltar.
PLAN_STALE_MIN = 10

# --- Bloqueio -----------------------------------------------------------------
# Durante um bloco, o que casar aqui é bloqueado: aba fechada (navegador) ou
# janela minimizada (apps). Fora dos blocos, só aparece como distração no
# painel "no PC hoje". Cada regra: (nome exibido, campo, regex). "title" olha o
# título da janela (é como se enxerga a aba ativa); "class" olha o WM_CLASS.
# Slack e Spotify ficam de fora de propósito (trabalho/comunidade e música).
BLOCK_RULES: list[tuple[str, str, re.Pattern]] = [
    # Vídeos terminam em "- YouTube"; a home é só "YouTube". Uma pesquisa no
    # Google por "youtube" não casa.
    ("YouTube", "title", re.compile(r"(^|\s[-–—]\s)YouTube(\s[-–—]\s|$)|^\(\d+\)\s*YouTube")),
    ("Instagram", "title", re.compile(r"\bInstagram\b")),
    ("Twitter", "title", re.compile(r"\s/\sX(\s[-–—]\s|$)|\bon X:|\bTwitter\b")),
    # Canal: "Channel - Twitch"; home: "Twitch"; VOD/clipe: "... on Twitch".
    ("Twitch", "title", re.compile(r"(^|\s[-–—]\s)Twitch(\s[-–—]\s|$)|^\(\d+\)\s*Twitch|\son Twitch(\s[-–—]\s|$)")),
    # Home: "TikTok - Make Your Day" (às vezes "(4)TikTok"); perfil/vídeo/busca: "... | TikTok".
    ("TikTok", "title", re.compile(r"^(\(\d+\)\s*)?TikTok(\s[-–—|]\s|$)|\s\|\sTikTok(\s|$)")),
    # Home: "Reddit - Dive into anything"; post: "Título : r/linux"; sub: "r/linux";
    # link compartilhado: "r/linux on Reddit: ...". Pesquisa no Google por "reddit" não casa.
    ("Reddit", "title", re.compile(r"(^(\(\d+\)\s*)?|\s[-–—|]\s)Reddit(\s[-–—|]\s|$)|\s:\sr/\w+(\s[-–—]\s|$)|^(\(\d+\)\s*)?r/\w+(\s[-–—:]\s|$)|\bon Reddit:")),
    ("Discord", "class", re.compile(r"^discord$", re.I)),
    ("Discord", "title", re.compile(r"\sDiscord(\s[-–—]\s|$)")),  # discord.com no navegador
    ("WhatsApp", "class", re.compile(r"^(whatsie|com\.ktechpit\.whatsie)$", re.I)),
    ("WhatsApp", "title", re.compile(r"^(\(\d+\)\s*)?WhatsApp(\s[-–—]\s|$)")),  # web.whatsapp.com
    ("Jogos", "class", re.compile(r"^(steam_app_\d+|steam|steamwebhelper|lutris|net\.lutris\.lutris|heroic|minecraft.*|retroarch|osu!?)$", re.I)),
]

# --- Distração barata ----------------------------------------------------------
# Rolar feed e vídeo curto. Instagram, Twitter, TikTok e Reddit são sempre baratas. No YouTube
# o título da janela não diz se é um Short, então a regra é por tempo: um
# vídeo que somou menos de YOUTUBE_SHORT_S no dia, ou a home/feed, é barata;
# vídeo longo é distração normal.
CHEAP_LABELS = {"Instagram", "Twitter", "TikTok", "Reddit"}
YOUTUBE_SHORT_S = 90
YOUTUBE_FEED = re.compile(r"^(\(\d+\)\s*)?YouTube(\s[-–—]\s|$)|#shorts\b", re.I)

# Navegadores: distração aqui = fechar a aba (Ctrl+W). Fora deles = minimizar.
BROWSER_CLASSES = re.compile(r"^(zen|app\.zen_browser\.zen|firefox|brave-browser|google-chrome|chromium)$", re.I)

HABITS = ("leitura", "academia", "faculdade")
WEEKLY_GOALS = {"academia": 3, "leitura": 7}

# Sincronização com Turso / libSQL (opcional: sincroniza tarefas com o bot da Azure)
# Lê do ambiente ou carrega do .env / agent/.env se existirem
for _env_candidate in (Path(__file__).parent.parent / ".env", Path(__file__).parent.parent / "agent" / ".env"):
    if _env_candidate.exists():
        try:
            for _line in _env_candidate.read_text().splitlines():
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
        except Exception:
            pass

TURSO_URL = os.environ.get("TURSO_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "")

