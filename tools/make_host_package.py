"""Make the upload package for Discord bot hosting sites (Pterodactyl panels).

  python tools/make_host_package.py              # with your settings (backup.zip, no keys)
  python tools/make_host_package.py --no-settings

Writes dist/hosting/FreeDiscordBot-hosting.zip. No key or token goes into the zip: secrets.env.example only lists
the names; you paste the values on the hosting site.
"""

import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "dist" / "hosting" / "FreeDiscordBot-hosting.zip"

OPTIONAL = [
    ("anthropic_key", "Claude (AI chat)"),
    ("openai_key", "ChatGPT (AI chat)"),
    ("xai_key", "Grok (AI chat)"),
    ("twitch_client_id", "Twitch alerts"),
    ("twitch_client_secret", "Twitch alerts"),
    ("roblox_api_key", "Roblox player sounds"),
    ("eulerstream_key", "TikTok total likes"),
    ("youtube_api_key", "/youtube"),
    ("instagram_api_key", "/instagram"),
]


def has_key(name: str) -> bool:
    try:
        import keyring

        return bool(keyring.get_password("DiscordBotManager", name))
    except Exception:  # noqa: BLE001
        return False


def extra_accounts(db: Path) -> list[dict]:
    if not db.exists():
        return []
    from botd.store import Store

    return Store(db).get_kv("bot_accounts", []) or []


def secrets_example(accounts: list[dict]) -> str:
    lines = ["# Paste your keys after the = signs, then rename this file to secrets.env.",
             "# הדבק את המפתחות אחרי סימני ה-=, ואז שנה את שם הקובץ ל-secrets.env.",
             "# Never share this file. / אל תשתף את הקובץ הזה.", "",
             "# Your bot token (Discord Developer Portal -> Bot -> Reset Token)", "BOTD_BOT_TOKEN=", ""]
    for acc in accounts:
        lines += [f"# Extra bot: {acc.get('label') or acc['id']}", f"BOTD_BOT_TOKEN_{acc['id'].upper()}=", ""]
    used = [(n, what) for n, what in OPTIONAL if has_key(n)]
    rest = [(n, what) for n, what in OPTIONAL if (n, what) not in used]
    if used:
        lines += ["# Keys you use in the app on your PC (copy them from there):", ""]
        for n, what in used:
            lines += [f"# {what}", f"BOTD_{n.upper()}=", ""]
    if rest:
        lines += ["# Optional, not set on your PC (remove the # at the start to use one):", ""]
        for n, what in rest:
            lines += [f"# {what}", f"# BOTD_{n.upper()}=", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    from botd import paths

    db = paths.db_file()
    with_settings = "--no-settings" not in sys.argv
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = OUT.with_suffix(".tmp")
    with zipfile.ZipFile(tmp_out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted((ROOT / "botd").rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                zf.write(f, f.relative_to(ROOT).as_posix())
        zf.write(ROOT / "requirements-server.txt", "requirements.txt")
        zf.write(ROOT / "hosting" / "main.py", "main.py")
        zf.write(ROOT / "hosting" / "STEPS.txt", "STEPS.txt")
        zf.writestr("secrets.env.example", secrets_example(extra_accounts(db)))
        if with_settings and db.exists():
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "botd.sqlite3"
                src, dst = sqlite3.connect(db), sqlite3.connect(copy)
                with dst:
                    src.backup(dst)  # consistent copy even while the bot runs
                src.close()
                dst.close()
                inner = Path(tmp) / "backup.zip"
                with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as bz:
                    bz.write(copy, "botd.sqlite3")
                zf.write(inner, "backup.zip")
    tmp_out.replace(OUT)
    size = OUT.stat().st_size / 1024
    print(f"Package ready: {OUT} ({size:.0f} KB){' with your settings' if with_settings and db.exists() else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
