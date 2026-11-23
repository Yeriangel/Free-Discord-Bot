"""Log in with Discord (for the Home panel): who you are and which servers you are in.

Uses Discord's implicit OAuth2 grant, so no Client Secret is needed: Discord sends the browser back to a small page on
this computer (http://127.0.0.1:53134/discord-callback, added once in the Developer Portal under OAuth2 > Redirects),
that page hands the temporary login to the bot service, and the service keeps it in Windows Credential Manager.
The login only allows reading your name, picture and server list (scopes: identify, guilds), and it expires by itself.
"""
import asyncio
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import secrets_store
from .i18n import tr

log = logging.getLogger("botd.discord_login")

CALLBACK_PORT = 53134
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/discord-callback"
SCOPES = "identify guilds"
API = "https://discord.com/api/v10"
SECRET_NAME = "discord_user_login"  # {"token": ..., "expires": ...} as JSON
LOGIN_TIMEOUT = 300

PERM_ADMIN = 1 << 3
PERM_MANAGE_GUILD = 1 << 5

CALLBACK_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Free Discord Bot</title>
<style>body{font-family:Segoe UI,Arial;background:#0d0f14;color:#e8e9ee;display:flex;align-items:center;justify-content:center;
height:100vh;margin:0}div{text-align:center}h1{font-size:22px}</style></head><body><div><h1 id="t">…</h1><p id="d"></p></div>
<script>
const p = new URLSearchParams(location.hash.slice(1));
const ok = (t, d) => { document.getElementById("t").textContent = t; document.getElementById("d").textContent = d; };
if (p.get("access_token")) {
  fetch("/discord-token", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({access_token: p.get("access_token"), expires_in: p.get("expires_in"), state: p.get("state")})})
  .then(r => r.ok ? ok("\\u2705 Logged in", "You can close this tab and go back to the app.")
                  : ok("Login failed", "Go back to the app and try again."));
} else {
  ok("Login cancelled", new URLSearchParams(location.search).get("error_description") || "Go back to the app and try again.");
}
</script></body></html>"""


class LoginFlow:
    """One login at a time: a short-lived local page on CALLBACK_PORT that receives the token from Discord."""

    def __init__(self):
        self.state = ""
        self.server: asyncio.base_events.Server | None = None
        self.done = asyncio.Event()
        self.error = ""

    def authorize_url(self, client_id: str) -> str:
        return "https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": client_id, "response_type": "token", "redirect_uri": REDIRECT_URI, "scope": SCOPES,
            "state": self.state})

    async def start(self, client_id: str) -> str:
        await self.stop()
        self.state = secrets.token_urlsafe(24)
        self.done = asyncio.Event()
        self.error = ""
        try:
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", CALLBACK_PORT)
        except OSError:
            raise ValueError(tr("הפורט {port} תפוס. סגור תוכנה אחרת שמשתמשת בו ונסה שוב.", port=CALLBACK_PORT)) from None
        asyncio.get_running_loop().call_later(LOGIN_TIMEOUT, lambda: asyncio.ensure_future(self.stop()))
        return self.authorize_url(client_id)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            self.server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            lines = head.decode("latin1").split("\r\n")
            method, target = lines[0].split(" ")[:2]
            headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
            path = urlparse(target).path
            if method == "GET" and path == "/discord-callback":
                return self._reply(writer, 200, CALLBACK_PAGE, "text/html; charset=utf-8")
            if method == "POST" and path == "/discord-token":
                length = min(int(headers.get("content-length") or 0), 4096)
                body = json.loads((await reader.readexactly(length)).decode() or "{}")
                if not secrets.compare_digest(str(body.get("state") or ""), self.state) or not body.get("access_token"):
                    return self._reply(writer, 403, "bad state", "text/plain")
                save_login(str(body["access_token"]), int(body.get("expires_in") or 604800))
                self.done.set()
                asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self.stop()))
                log.info(tr("התחברת לדיסקורד בפאנל הבית"))
                return self._reply(writer, 200, "ok", "text/plain")
            return self._reply(writer, 404, "not found", "text/plain")
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, json.JSONDecodeError):
            return self._reply(writer, 400, "bad request", "text/plain")
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _reply(writer: asyncio.StreamWriter, status: int, body: str, ctype: str) -> None:
        data = body.encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found"}.get(status, "OK")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\n"
                     f"Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + data)


def save_login(token: str, expires_in: int) -> None:
    secrets_store.set(SECRET_NAME, json.dumps({"token": token, "expires": time.time() + expires_in}))


def load_login() -> str | None:
    raw = secrets_store.get(SECRET_NAME)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if data.get("expires", 0) < time.time() + 60:
        return None
    return data.get("token")


def logout() -> None:
    secrets_store.delete(SECRET_NAME)


def can_manage(g: dict) -> bool:
    perms = int(g.get("permissions") or 0)
    return bool(g.get("owner")) or bool(perms & PERM_ADMIN) or bool(perms & PERM_MANAGE_GUILD)


def icon_url(g: dict) -> str:
    if not g.get("icon"):
        return ""
    ext = "gif" if str(g["icon"]).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.{ext}?size=128"


def avatar_url(u: dict) -> str:
    if u.get("avatar"):
        return f"https://cdn.discordapp.com/avatars/{u['id']}/{u['avatar']}.png?size=128"
    return f"https://cdn.discordapp.com/embed/avatars/{(int(u.get('id', 0)) >> 22) % 6}.png"


async def home(token: str, bot_guild_ids: set[str]) -> dict:
    """Your Discord user and servers, sorted: the bot is there / you can add it / other servers."""
    async with httpx.AsyncClient(timeout=15, headers={"Authorization": f"Bearer {token}"}) as client:
        me, guilds = await asyncio.gather(client.get(f"{API}/users/@me"), client.get(f"{API}/users/@me/guilds"))
    if me.status_code == 401 or guilds.status_code == 401:
        logout()
        raise PermissionError("expired")
    if me.status_code != 200 or guilds.status_code != 200:
        raise ValueError(tr("דיסקורד לא ענה כרגע. נסה שוב עוד מעט."))
    u = me.json()
    rows = []
    for g in guilds.json():
        rows.append({"id": g["id"], "name": g.get("name", ""), "icon": icon_url(g), "owner": bool(g.get("owner")),
                     "can_manage": can_manage(g), "bot_here": g["id"] in bot_guild_ids})
    rows.sort(key=lambda r: (not r["bot_here"], not r["can_manage"], r["name"].lower()))
    return {"user": {"id": u["id"], "name": u.get("global_name") or u.get("username", ""), "username": u.get("username", ""),
                     "avatar": avatar_url(u)}, "guilds": rows}
