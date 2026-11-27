import uvicorn

from . import config

if __name__ == "__main__":
    # Só escuta em localhost: o app não deve ser acessível pela rede.
    uvicorn.run("bentofocus.main:app", host="127.0.0.1", port=config.PORT, log_level="warning")
