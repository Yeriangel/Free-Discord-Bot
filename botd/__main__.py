"""Run the bot service:  python -m botd

Starts the local control API (and the bot, if "autostart" is on). Writes
%APPDATA%\\DiscordBotManager\\session.json with the port and a per-launch
access token so the UI window can connect. Only one instance runs at a time.
"""

import json
import logging
import logging.handlers
import os
import secrets
import socket
import sys

import uvicorn

from . import paths, secrets_store
from .api import create_app
from .bot import BotFleet
from .bus import BusLogHandler, bus
from .redact import RedactingFormatter
from .store import Store


def setup_logging() -> None:
    fmt = RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        paths.log_dir() / "botd.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
    root.addHandler(BusLogHandler(bus))
    for noisy in ("httpx", "httpcore", "websockets", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def read_session() -> dict | None:
    try:
        return json.loads(paths.session_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def session_alive(session: dict | None) -> bool:
    if not session:
        return False
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{session['port']}/api/ping",
        headers={"X-Session-Token": session["token"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except OSError:
        return False


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    if session_alive(read_session()):
        print("Bot service is already running.")
        return 0

    setup_logging()
    secrets_store.load_all()  # register secrets with the log redactor

    port = int(os.environ.get("BOTD_PORT") or free_port())
    token = secrets.token_urlsafe(32)
    store = Store(paths.db_file())
    from .i18n import set_ui_lang
    set_ui_lang(store.get_settings()["ui_lang"])
    manager = BotFleet(store)

    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server is not None:
            server.should_exit = True

    app = create_app(manager, store, token, port, request_shutdown)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, access_log=False, log_config=None, lifespan="on",
        timeout_graceful_shutdown=5,  # an old app window's live connection must not keep the service alive
    )
    server = uvicorn.Server(config)

    session_path = paths.session_file()
    tmp = session_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    os.replace(tmp, session_path)
    try:
        server.run()
    finally:
        current = read_session()
        if current and current.get("pid") == os.getpid():
            try:
                session_path.unlink()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
