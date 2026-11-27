"""Start file for Discord bot hosting sites (Pterodactyl panels: MonkeyBytes, Wispbyte, Kerit, HeavenCloud ...).

The panel runs:  python main.py
  - keys come from secrets.env next to this file (you create it from secrets.env.example in the panel);
  - settings come from backup.zip the first time (made by the app on your PC), then live in the data folder;
  - the bot starts by itself.
Nothing here prints a key.
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGES = HERE / ".packages"
NEEDED = ("discord", "fastapi", "uvicorn", "httpx", "anthropic", "keyring")


def say(he: str, en: str) -> None:
    print(f"[Free Discord Bot] {en}\n[Free Discord Bot] {he}", flush=True)


def load_secrets() -> list[str]:
    """KEY=VALUE lines from secrets.env -> environment. Returns the key names (never the values)."""
    found = []
    path = HERE / "secrets.env"
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()  # "KEY=value   # a note"
            if value.startswith("#"):
                value = ""
        if key and value and not os.environ.get(key):
            os.environ[key] = value
            found.append(key)
    return found


def ensure_packages() -> None:
    """Most panels install requirements.txt by themselves; if not, install the packages next to the bot."""
    if PACKAGES.is_dir():
        sys.path.insert(0, str(PACKAGES))
    try:
        for name in NEEDED:
            __import__(name)
        return
    except ImportError:
        pass
    say("מתקין את החבילות של הבוט (רק בפעם הראשונה, כמה דקות)...", "Installing the bot's packages (first start only, a few minutes)...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                           "--target", str(PACKAGES), "-r", str(HERE / "requirements.txt")])
    if str(PACKAGES) not in sys.path:
        sys.path.insert(0, str(PACKAGES))
    import importlib

    importlib.invalidate_caches()


def restart() -> None:
    """Run main.py again in the same process slot (the panel keeps showing it as running)."""
    say("מפעיל מחדש עם הגרסה החדשה...", "Restarting with the new version...")
    sys.stdout.flush()
    args = [sys.executable, str(HERE / "main.py")]
    if os.name == "nt":  # Windows' execv does not quote paths with spaces
        args = [f'"{a}"' for a in args]
    os.execv(sys.executable, args)


def sync_start_files() -> bool:
    """After an update: the server package list and this start file come from the new version. True = restart needed."""
    import shutil

    server_req, req = HERE / "requirements-server.txt", HERE / "requirements.txt"
    if server_req.is_file() and (not req.is_file() or req.read_bytes() != server_req.read_bytes()):
        shutil.copyfile(server_req, req)
        say("מתקין חבילות שהשתנו...", "Installing changed packages...")
        # --ignore-installed: install into .local only, never remove packages from anywhere else
        subprocess.call([sys.executable, "-m", "pip", "install", "--ignore-installed", "--disable-pip-version-check",
                         "--prefix", str(HERE / ".local"), "-r", str(req)])
    new_main = HERE / "hosting" / "main.py"
    if new_main.is_file() and new_main.read_bytes() != Path(__file__).read_bytes():
        shutil.copyfile(new_main, HERE / "main.py")
        return True
    return False


def prune_backups(keep: int = 2) -> None:
    import shutil

    from botd import paths

    folder = paths.data_dir() / "backups"
    if folder.is_dir():
        old = sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        for p in old[:-keep]:
            shutil.rmtree(p, ignore_errors=True)


def update_now() -> bool:
    """Install a newer GitHub release if there is one. True when something was installed."""
    import asyncio

    from botd import updater
    from botd.version import UPDATE_REPO, VERSION

    try:
        releases = asyncio.run(updater.list_releases(UPDATE_REPO))
        newest = next((r for r in releases if not r["prerelease"] and r["zip_url"]), None)
        if not newest or not newest["is_newer"]:
            return False
        say(f"נמצאה גרסה חדשה: {newest['tag']} (עכשיו {VERSION}). מעדכן...",
            f"New version found: {newest['tag']} (running {VERSION}). Updating...")
        asyncio.run(updater.install(UPDATE_REPO, newest["tag"]))
        prune_backups()
        return True
    except Exception as e:  # noqa: BLE001 - a failed update must never stop the bot
        say(f"העדכון נכשל, ממשיך עם הגרסה הנוכחית: {e}", f"Update failed, staying on the current version: {e}")
        return False


def update_loop(hours: float) -> None:
    import time

    while True:
        time.sleep(max(0.25, hours) * 3600)
        if update_now():
            sync_start_files()
            restart()


def main() -> int:
    if sys.version_info < (3, 10):
        say("צריך Python 3.10 ומעלה. בפאנל, בעמוד Startup, בחר גרסת Python חדשה יותר (3.11 או 3.12).",
            "Python 3.10 or newer is needed. In the panel's Startup page, pick a newer Python version (3.11 or 3.12).")
        return 1
    os.chdir(HERE)
    sys.path.insert(0, str(HERE))
    keys = load_secrets()
    os.environ.setdefault("BOTD_DATA_DIR", str(HERE / "data"))
    os.environ.setdefault("BOTD_AUTOSTART", "1")      # always on
    os.environ.setdefault("BOTD_MOBILE", "0")         # no phone listener on a hosting site
    os.environ.setdefault("BOTD_REVEAL", "0")
    os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")  # keys come from secrets.env

    if not os.environ.get("BOTD_BOT_TOKEN"):
        say("חסר הטוקן של הבוט. בפאנל: File Manager, שנה את השם של secrets.env.example ל-secrets.env, "
            "הדבק את הטוקן אחרי BOTD_BOT_TOKEN= ושמור. ואז Restart.",
            "The bot token is missing. In the panel: File Manager, rename secrets.env.example to secrets.env, "
            "paste your token after BOTD_BOT_TOKEN= and save. Then Restart.")
        return 1
    say(f"נטענו {len(keys)} מפתחות מ-secrets.env.", f"Loaded {len(keys)} keys from secrets.env: {', '.join(keys)}")

    ensure_packages()

    # Auto-update from GitHub releases: at start and every few hours (BOTD_AUTO_UPDATE=0 turns it off).
    os.environ.setdefault("BOTD_UPDATER_PIP", "0")
    if os.environ.get("BOTD_AUTO_UPDATE", "1") != "0":
        if sync_start_files():  # a newer start file arrived with the last update
            restart()
        if update_now():
            sync_start_files()
            restart()
        import threading

        hours = float(os.environ.get("BOTD_UPDATE_EVERY_HOURS") or 3)
        threading.Thread(target=update_loop, args=(hours,), daemon=True, name="auto-update").start()
        say(f"עדכונים אוטומטיים פעילים: בודק ב-GitHub כל {hours:g} שעות.",
            f"Auto-update is on: checking GitHub every {hours:g} hours.")

    from botd import paths
    from botd.__main__ import import_backup, main as run

    backup = HERE / "backup.zip"
    if backup.is_file() and not paths.db_file().exists():
        say("מייבא את ההגדרות מ-backup.zip...", "Importing your settings from backup.zip...")
        if import_backup(str(backup)) == 0:
            backup.replace(HERE / "backup-imported.zip")
    say("מפעיל את הבוט...", "Starting the bot...")
    return run()


if __name__ == "__main__":
    sys.exit(main())
