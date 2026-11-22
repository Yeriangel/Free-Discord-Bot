"""Start the bot service (no window) when you sign in to Windows, via a small script in your Startup folder."""

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
FILE_NAME = "FreeDiscordBot.vbs"


def startup_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def script_path() -> Path:
    return startup_dir() / FILE_NAME


def script_text() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    q = lambda p: str(p).replace('"', '""')  # noqa: E731 - VBScript string escaping
    return (
        "' Starts the Free Discord Bot service in the background (created by the app; turn it off in the app)\r\n"
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{q(APP_DIR)}"\r\n'
        f'sh.Run """{q(runner)}"" -m botd", 0, False\r\n'
    )


def is_enabled() -> bool:
    return script_path().exists()


def set_enabled(on: bool) -> None:
    if sys.platform != "win32":
        raise ValueError("Windows only")
    path = script_path()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(script_text(), encoding="utf-8")
    elif path.exists():
        path.unlink()
