"""Phone access (the Android app): pairing, Discord login check, and which connections are allowed.

When "Phone access" is on, the bot service also listens on a fixed port (default 8765), but only answers devices on
your Tailscale private network (addresses 100.64.0.0/10) that you paired from the PC app, and only after they logged
in with the Discord account that owns the bot (or one of the allowed users). Keys are never shown in full on a phone.
"""
import hashlib
import ipaddress
import logging
import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .i18n import tr

log = logging.getLogger("botd.mobile")

TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
DEFAULT_PORT = 8765
PAIR_CODE_SECONDS = 600
DEVICES_KEY = "mobile_devices"
MAX_DEVICES = 10
APP_SCHEME = "freediscordbot"
DISCORD_REDIRECT = f"{APP_SCHEME}://discord-callback"
# what a phone may not do: things that only make sense on the PC itself
BLOCKED_FOR_PHONES = ("/api/backup", "/api/open-url", "/api/home/login", "/api/app/restart", "/api/mobile/pair",
                      "/api/mobile/devices")


def is_tailscale(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "") in TAILSCALE_NET
    except ValueError:
        return False


def tailscale_ip() -> str | None:
    """This computer's Tailscale address (100.x.y.z), or None when Tailscale is not running."""
    exe = shutil.which("tailscale") or next((p for p in (r"C:\Program Files\Tailscale\tailscale.exe",) if Path(p).exists()), None)
    if exe:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.split()
            for ip in out:
                if is_tailscale(ip):
                    return ip
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if is_tailscale(info[4][0]):
                return info[4][0]
    except OSError:
        pass
    return None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class MobileAccess:
    def __init__(self, store):
        self.store = store
        self.codes: dict[str, float] = {}  # pairing code -> expiry

    # ---- devices -------------------------------------------------------------------------------------------------
    def devices(self) -> list[dict]:
        return list(self.store.get_kv(DEVICES_KEY, []) or [])

    def _save(self, devices: list[dict]) -> None:
        self.store.set_kv(DEVICES_KEY, devices)

    def public_devices(self) -> list[dict]:
        return [{k: d.get(k) for k in ("id", "name", "created", "last_seen", "discord_name", "verified")} for d in self.devices()]

    def device_for(self, token: str | None) -> dict | None:
        if not token:
            return None
        h = _hash(token)
        return next((d for d in self.devices() if secrets.compare_digest(d.get("token_hash", ""), h)), None)

    def touch(self, device_id: str) -> None:
        devices = self.devices()
        for d in devices:
            if d["id"] == device_id and time.time() - d.get("last_seen", 0) > 60:
                d["last_seen"] = time.time()
                self._save(devices)
                return

    def remove(self, device_id: str) -> bool:
        devices = self.devices()
        left = [d for d in devices if d["id"] != device_id]
        self._save(left)
        return len(left) != len(devices)

    # ---- pairing -------------------------------------------------------------------------------------------------
    def new_code(self) -> str:
        now = time.time()
        self.codes = {c: exp for c, exp in self.codes.items() if exp > now}
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.codes[code] = now + PAIR_CODE_SECONDS
        return code

    def claim(self, code: str, name: str) -> str:
        """A phone uses the pairing code shown on the PC -> its own long device token (shown once)."""
        exp = self.codes.pop(str(code).strip(), None)
        if not exp or exp < time.time():
            raise ValueError(tr("קוד החיבור לא נכון או שפג תוקפו. צור קוד חדש באפליקציה במחשב."))
        devices = self.devices()
        if len(devices) >= MAX_DEVICES:
            raise ValueError(tr("כבר מחוברים {n} טלפונים. הסר אחד באפליקציה במחשב.", n=MAX_DEVICES))
        token = secrets.token_urlsafe(32)
        devices.append({"id": secrets.token_hex(6), "name": (name or "Phone").strip()[:60], "token_hash": _hash(token),
                        "created": time.time(), "last_seen": time.time(), "verified": False, "discord_id": "",
                        "discord_name": ""})
        self._save(devices)
        log.info(tr("טלפון חדש חובר: %s"), (name or "Phone")[:60])
        return token

    def verify(self, device_id: str, discord_user: dict, allowed_ids: set[str]) -> None:
        """After the Discord login on the phone: only the bot's owner or an allowed user may control the bot."""
        uid = str(discord_user.get("id") or "")
        if not uid or uid not in allowed_ids:
            raise PermissionError(tr("החשבון הזה לא יכול לשלוט בבוט. התחבר עם החשבון שבבעלותו הבוט."))
        devices = self.devices()
        for d in devices:
            if d["id"] == device_id:
                d.update(verified=True, discord_id=uid,
                         discord_name=discord_user.get("global_name") or discord_user.get("username") or uid)
        self._save(devices)
        log.info(tr("טלפון אומת עם דיסקורד: %s"), discord_user.get("username") or uid)


def pair_link(base_url: str, code: str) -> str:
    from urllib.parse import quote

    return f"{APP_SCHEME}://pair?url={quote(base_url, safe='')}&code={code}"


def mobile_port(settings: dict) -> int:
    try:
        port = int(settings.get("mobile_port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return port if 1024 <= port <= 65535 else DEFAULT_PORT


def enabled(settings: dict) -> bool:
    return bool(settings.get("mobile_enabled")) or os.environ.get("BOTD_MOBILE") == "1"
