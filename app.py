"""Desktop window for the bot manager.

Starts the bot service (botd) in the background if it is not already running,
then opens the dashboard in a native window (Edge WebView2). Closing the window
stops the service, unless "keep running in background" is turned on.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import webview

from botd import paths
from botd.__main__ import read_session, session_alive

ROOT = Path(__file__).resolve().parent


def _call(session: dict, method: str, path: str) -> dict | None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{session['port']}/api{path}",
        method=method,
        headers={"X-Session-Token": session["token"], "Content-Type": "application/json"},
        data=b"{}" if method == "POST" else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read() or b"{}")
    except OSError:
        return None


def ensure_service(timeout: float = 20.0) -> dict:
    session = read_session()
    if session_alive(session):
        return session

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = str(pythonw if pythonw.exists() else exe)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [runner, "-m", "botd"],
        cwd=str(ROOT),
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=open(paths.log_dir() / "botd-stderr.log", "ab"),
        close_fds=True,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        session = read_session()
        if session_alive(session):
            return session
    raise RuntimeError(f"The bot service did not start. Check the log: {paths.log_dir() / 'botd.log'}")


def main() -> int:
    try:
        session = ensure_service()
    except RuntimeError as exc:
        webview.create_window("Bot Manager: error", html=f"<body style='font-family:Segoe UI;padding:24px'>{exc}</body>")
        webview.start()
        return 1

    url = f"http://127.0.0.1:{session['port']}/#token={session['token']}"
    window = webview.create_window(
        "Discord Bot Manager",
        url,
        width=1320,
        height=860,
        min_size=(980, 640),
        background_color="#0d0f14",
        text_select=True,
    )

    def on_closed():
        settings = _call(session, "GET", "/settings") or {}
        if not settings.get("settings", {}).get("keep_running_on_close"):
            _call(session, "POST", "/shutdown")

    window.events.closed += on_closed
    webview.start(private_mode=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
