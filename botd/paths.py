"""Where the app keeps its data: %APPDATA%\\DiscordBotManager (outside the project, never in git)."""

import os
from pathlib import Path

APP_NAME = "DiscordBotManager"


def data_dir() -> Path:
    base = os.environ.get("BOTD_DATA_DIR") or os.path.join(
        os.environ.get("APPDATA", str(Path.home())), APP_NAME
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_file() -> Path:
    return data_dir() / "session.json"


def db_file() -> Path:
    return data_dir() / "botd.sqlite3"


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
