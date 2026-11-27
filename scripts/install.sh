#!/usr/bin/env bash
# Instala o BentoFocus para o usuário atual: serviço systemd (sobe no login)
# e atalho no menu de aplicativos. Pode rodar de novo sem problema.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
APP_DIR="$HOME/.local/share/applications"

echo "→ dependências (uv sync)"
(cd "$ROOT" && uv sync --quiet)

echo "→ serviço systemd de usuário"
mkdir -p "$UNIT_DIR"
sed "s|@ROOT@|$ROOT|g" "$ROOT/deploy/bentofocus.service" > "$UNIT_DIR/bentofocus.service"
systemctl --user daemon-reload
systemctl --user enable --now bentofocus.service
systemctl --user restart bentofocus.service

echo "→ atalho no menu"
mkdir -p "$APP_DIR"
sed "s|@ROOT@|$ROOT|g" "$ROOT/deploy/bentofocus.desktop" > "$APP_DIR/bentofocus.desktop"
update-desktop-database "$APP_DIR" 2>/dev/null || true

echo "→ painel abre sozinho ao entrar na sessão"
mkdir -p "$HOME/.config/autostart"
sed "s|@ROOT@|$ROOT|g" "$ROOT/deploy/bentofocus-autostart.desktop" > "$HOME/.config/autostart/bentofocus.desktop"

sleep 2
if curl -sf "http://127.0.0.1:7777/api/collector" >/dev/null; then
  echo "✓ rodando em http://127.0.0.1:7777. Abra 'BentoFocus' pelo menu."
else
  echo "✗ o serviço não respondeu. Veja: journalctl --user -u bentofocus -e"
  exit 1
fi
