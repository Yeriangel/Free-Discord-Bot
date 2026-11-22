"""Desktop window for the bot manager.

Starts the bot service (botd) in the background if it is not already running,
then opens the dashboard in a native window (Edge WebView2). Closing the window
stops the service, unless "keep running in background" is turned on.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import webview

from botd import paths
from botd.__main__ import read_session, session_alive

ROOT = Path(__file__).resolve().parent
ICON = str(ROOT / "ui" / "logo.ico")


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


def running_build(session: dict | None) -> str | None:
    """Version + code fingerprint of an already running service (None if none is running)."""
    if not session_alive(session):
        return None
    ping = _call(session, "GET", "/ping") or {}
    return f"{ping.get('version', '')}/{ping.get('build', '')}"


def stop_old_service(session: dict, timeout: float = 15.0) -> None:
    """Asks the old service to stop and waits; if it is still running after that, ends its process."""
    _call(session, "POST", "/shutdown")
    deadline = time.time() + timeout
    while time.time() < deadline and session_alive(session):
        time.sleep(0.3)
    if session_alive(session) and session.get("pid"):
        try:
            os.kill(int(session["pid"]), 9)  # on Windows this ends the process (TerminateProcess)
        except OSError:
            pass
        time.sleep(1)


def ensure_service(timeout: float = 20.0, force_restart: bool = False) -> dict:
    from botd import __version__, build_id

    session = read_session()
    running = running_build(session)
    if running is not None and running == f"{__version__}/{build_id()}" and not force_restart:
        return session
    if running is not None:  # the service runs older code than the files on disk (update or new build): restart it
        stop_old_service(session)

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


def launch_detached(*args: str) -> None:
    """Starts another copy of this launcher (used by the app's "Restart now" button after an update)."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(ROOT / "app.py"), *args], cwd=str(ROOT),
                     creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)


def ensure_start_menu_shortcut() -> None:
    """Adds "Free Discord Bot" (with the app logo) to the Start menu once, so the app can be opened from there."""
    if os.name != "nt":
        return
    try:
        from botd.shortcut import create_shortcuts
        create_shortcuts(desktop=False, only_if_missing=True)
    except Exception:  # noqa: BLE001 - a missing shortcut must never stop the app
        pass


def set_windows_app_id() -> None:
    """Gives the window its own taskbar entry and icon instead of grouping it under Python."""
    if os.name == "nt":
        try:
            import ctypes
            from botd.shortcut import APP_ID
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    set_windows_app_id()
    try:
        session = ensure_service(force_restart="--restart" in sys.argv)
    except RuntimeError as exc:
        webview.create_window("Bot Manager: error", html=f"<body style='font-family:Segoe UI;padding:24px'>{exc}</body>")
        webview.start(icon=ICON)
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

    replaced = threading.Event()

    def watch_for_newer_window():
        """After "Restart now", a new launcher starts a new service and window; this old window then closes itself."""
        while not replaced.is_set():
            time.sleep(1.5)
            current = read_session()
            if current and current.get("token") != session["token"] and session_alive(current):
                replaced.set()
                try:
                    window.destroy()
                except Exception:  # noqa: BLE001 - the window may already be gone
                    pass

    def on_closed():
        if replaced.is_set():
            return
        replaced.set()
        settings = _call(session, "GET", "/settings") or {}
        if not settings.get("settings", {}).get("keep_running_on_close"):
            _call(session, "POST", "/shutdown")

    window.events.closed += on_closed
    threading.Thread(target=watch_for_newer_window, daemon=True).start()
    threading.Thread(target=ensure_start_menu_shortcut, daemon=True).start()
    webview.start(private_mode=True, icon=ICON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
