from core.config.loader import get_config
from app.bootstrap import compose_trader_app

def main():
    cfg = get_config()
    app = compose_trader_app(cfg)
    app.start()


if __name__ == "__main__":
    main()
