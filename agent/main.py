import logging
from . import db
from .bot import create_bot_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("agent.main")


def main():
    logger.info("Iniciando BentoFocus Telegram Agent...")
    db.init_db()
    app = create_bot_app()
    logger.info("Bot conectado ao Telegram! Aguardando mensagens...")
    app.run_polling()


if __name__ == "__main__":
    main()

