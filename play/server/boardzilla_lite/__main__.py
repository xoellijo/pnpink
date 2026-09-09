import argparse
from pathlib import Path

from aiohttp import web

from .app import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PnPInk Play local server")
    parser.add_argument("--games", action="append", required=True, type=Path)
    parser.add_argument("--database", type=Path, default=Path("data/pnpink-play.db"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8080, type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = create_app(args.games, args.database)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

