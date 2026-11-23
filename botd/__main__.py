"""Run the bot service:  python -m botd

Starts the local control API (and the bot, if "autostart" is on). Writes
%APPDATA%\\DiscordBotManager\\session.json with the port and a per-launch
access token so the UI window can connect. Only one instance runs at a time.
"""

import asyncio
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


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_for_old_service(port: int, timeout: float = 40.0) -> bool:
    """Live reload after an update: the old service is shutting down; wait until it is gone and its port is free."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not session_alive(read_session()) and port_free(port):
            return True
        time.sleep(0.25)
    return False


def print_url() -> int:
    """python -m botd url  -> the dashboard link of the running service (on a server: open it through an SSH tunnel)."""
    session = read_session()
    if not session_alive(session):
        print("The bot service is not running.")
        return 1
    print(f"http://127.0.0.1:{session['port']}/#token={session['token']}")
    return 0


def import_backup(path: str) -> int:
    """python -m botd import backup.zip  -> puts the settings from the app's backup into this machine's data folder."""
    import zipfile

    if session_alive(read_session()):
        print("Stop the bot service first, then import.")
        return 1
    with zipfile.ZipFile(path) as zf:
        if "botd.sqlite3" not in zf.namelist():
            print("This is not a Free Discord Bot backup.")
            return 1
        target = paths.db_file()
        if target.exists():
            target.replace(target.with_suffix(".before-import"))
        target.write_bytes(zf.read("botd.sqlite3"))
    print(f"Settings imported into {target}")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "url":
        return print_url()
    if len(sys.argv) > 2 and sys.argv[1] == "import":
        return import_backup(sys.argv[2])
    takeover = os.environ.pop("BOTD_TAKEOVER", "") == "1"
    if takeover:
        wanted = int(os.environ.get("BOTD_PORT") or 0)
        if not wait_for_old_service(wanted):
            os.environ.pop("BOTD_PORT", None)  # the port stayed busy: use a new one (the window follows it)
    elif session_alive(read_session()):
        print("Bot service is already running.")
        return 0

    setup_logging()
    secrets_store.load_all()  # register secrets with the log redactor

    port = int(os.environ.pop("BOTD_PORT", "") or free_port())
    token = os.environ.pop("BOTD_TOKEN", "") or secrets.token_urlsafe(32)
    store = Store(paths.db_file())
    from .i18n import set_ui_lang
    set_ui_lang(store.get_settings()["ui_lang"])
    manager = BotFleet(store)

    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server is not None:
            server.should_exit = True
        if phone_server is not None:
            phone_server.should_exit = True

    app = create_app(manager, store, token, port, request_shutdown)
    # BOTD_HOST=0.0.0.0 only inside Docker (the port is published to 127.0.0.1 of the server, see HOSTING.md)
    config = uvicorn.Config(
        app, host=os.environ.get("BOTD_HOST", "127.0.0.1"), port=port, access_log=False, log_config=None, lifespan="on",
        timeout_graceful_shutdown=5,  # an old app window's live connection must not keep the service alive
    )
    server = uvicorn.Server(config)
    phone_server = None
    from . import mobile

    settings = store.get_settings()
    if mobile.enabled(settings):
        # Listens on every network address; each request is then checked: only paired phones, and only from your
        # Tailscale network (or the home Wi-Fi when you allowed that). Tailscale can start later, no restart needed.
        phone_server = uvicorn.Server(uvicorn.Config(
            app, host=os.environ.get("BOTD_MOBILE_HOST") or "0.0.0.0", port=mobile.mobile_port(settings),
            access_log=False, log_config=None, lifespan="off", timeout_graceful_shutdown=5))

    session_path = paths.session_file()
    tmp = session_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid()}), encoding="utf-8")
    os.replace(tmp, session_path)
    async def serve_all() -> None:
        jobs = [server.serve()]
        if phone_server is not None:
            jobs.append(phone_server.serve())
        await asyncio.gather(*jobs)

    try:
        asyncio.run(serve_all())
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
