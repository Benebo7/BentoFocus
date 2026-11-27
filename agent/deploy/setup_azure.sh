#!/usr/bin/env bash
set -e

echo "=== BentoFocus Agent Setup ==="

# Garante que está no diretório do agent
if [ ! -f "requirements.txt" ]; then
    echo "Erro: execute este script dentro da pasta agent/!"
    exit 1
fi

AGENT_DIR="$(pwd)"
REPO_DIR="$(cd .. && pwd)"
CURRENT_USER="${SUDO_USER:-$(id -un)}"

# Verifica onde está o Python do venv
VENV_PYTHON=""
if [ -f "$AGENT_DIR/.venv/bin/python" ]; then
    VENV_PYTHON="$AGENT_DIR/.venv/bin/python"
elif [ -f "$REPO_DIR/.venv/bin/python" ]; then
    VENV_PYTHON="$REPO_DIR/.venv/bin/python"
else
    echo "Criando ambiente virtual isolado em $AGENT_DIR/.venv..."
    python3 -m venv "$AGENT_DIR/.venv"
    VENV_PYTHON="$AGENT_DIR/.venv/bin/python"
fi

echo "Instalando dependências com $VENV_PYTHON..."
"$VENV_PYTHON" -m pip install --upgrade pip --quiet || true
"$VENV_PYTHON" -m pip install -r requirements.txt --quiet

# Garante existência do arquivo .env
if [ ! -f "$AGENT_DIR/.env" ]; then
    if [ -f "$REPO_DIR/.env" ]; then
        echo "Copiando .env da raiz do projeto..."
        cp "$REPO_DIR/.env" "$AGENT_DIR/.env"
    else
        echo "Criando .env a partir de .env.example..."
        cp "$AGENT_DIR/.env.example" "$AGENT_DIR/.env"
        echo "ATENÇÃO: Preencha suas chaves no arquivo $AGENT_DIR/.env!"
    fi
fi

# Gera o arquivo de serviço systemd com os caminhos REAIS da máquina atual
SERVICE_DST="/etc/systemd/system/bentofocus-agent.service"

echo "Configurando serviço systemd em $SERVICE_DST para o usuário '$CURRENT_USER'..."
sudo tee "$SERVICE_DST" > /dev/null <<EOF
[Unit]
Description=BentoFocus Telegram Agent
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${VENV_PYTHON} -m agent.main
Restart=always
RestartSec=5
EnvironmentFile=${AGENT_DIR}/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable bentofocus-agent

echo ""
echo "=== Configurado com sucesso! ==="
echo "Para iniciar o serviço:"
echo "  sudo systemctl restart bentofocus-agent"
echo ""
echo "Para ver os logs em tempo real:"
echo "  sudo journalctl -u bentofocus-agent -f"
