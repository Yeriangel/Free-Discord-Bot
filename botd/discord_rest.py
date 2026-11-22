"""Direct Discord REST calls that work whether or not the bot is connected."""

from .i18n import tr
import base64
import binascii
import logging

import httpx

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/Rapptz/discord.py, 2.x) DiscordBotManager/0.1"
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_AVATAR_BYTES = 8 * 1024 * 1024

log = logging.getLogger("botd.rest")


class DiscordAPIError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def _flatten_errors(errors, out: list[str]) -> None:
    if isinstance(errors, dict):
        for key, value in errors.items():
            if key == "_errors" and isinstance(value, list):
                out.extend(e.get("message", "") for e in value if isinstance(e, dict))
            else:
                _flatten_errors(value, out)


async def _request(method: str, path: str, token: str, json: dict | None = None) -> dict:
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.request(method, API + path, headers=headers, json=json)
    except httpx.HTTPError as exc:
        raise DiscordAPIError(0, tr("אין חיבור לדיסקורד ({error})", error=type(exc).__name__)) from None

    if resp.status_code == 429:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        retry = float(data.get("retry_after") or resp.headers.get("Retry-After") or 0)
        log.warning("Discord rate limit on %s %s, retry after %.0fs", method, path, retry)
        raise DiscordAPIError(429, tr("דיסקורד הגביל את הקצב. נסה שוב מאוחר יותר."), retry_after=retry)
    if resp.status_code == 401:
        raise DiscordAPIError(401, tr("הטוקן לא תקין או שבוטל. בדוק אותו ב-Developer Portal."))
    if resp.status_code >= 400:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        details: list[str] = []
        _flatten_errors(data.get("errors"), details)
        message = "; ".join(d for d in details if d) or data.get("message") or f"HTTP {resp.status_code}"
        raise DiscordAPIError(resp.status_code, tr("דיסקורד דחה את הבקשה: {message}", message=message))
    return resp.json() if resp.content else {}


async def get_me(token: str) -> dict:
    return await _request("GET", "/users/@me", token)


async def get_application(token: str) -> dict:
    return await _request("GET", "/oauth2/applications/@me", token)


def validate_avatar_data_uri(data_uri: str) -> str:
    """Checks a data: URI from the UI and returns it in the form Discord accepts."""
    if not data_uri.startswith("data:") or ";base64," not in data_uri:
        raise ValueError(tr("קובץ התמונה לא תקין"))
    header, b64 = data_uri.split(",", 1)
    mime = header[5:].split(";", 1)[0].lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ValueError(tr("סוג קובץ לא נתמך. אפשר PNG, JPG, GIF או WEBP."))
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(tr("קובץ התמונה פגום")) from None
    if len(raw) > MAX_AVATAR_BYTES:
        raise ValueError(tr("התמונה גדולה מדי (עד 8MB)"))
    return f"data:{mime};base64,{b64}"


async def update_me(token: str, username: str | None = None, avatar_data_uri: str | None = None,
                    banner_data_uri: str | None = None) -> dict:
    payload: dict = {}
    if username is not None:
        username = username.strip()
        if not 2 <= len(username) <= 32:
            raise ValueError(tr("שם הבוט צריך להיות באורך 2–32 תווים"))
        payload["username"] = username
    if avatar_data_uri is not None:
        payload["avatar"] = validate_avatar_data_uri(avatar_data_uri)
    if banner_data_uri is not None:
        payload["banner"] = validate_avatar_data_uri(banner_data_uri)
    if not payload:
        raise ValueError(tr("אין מה לעדכן"))
    return await _request("PATCH", "/users/@me", token, json=payload)


def banner_url(user: dict) -> str:
    if user.get("banner"):
        ext = "gif" if str(user["banner"]).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/banners/{user['id']}/{user['banner']}.{ext}?size=600"
    return ""


def avatar_url(user: dict) -> str:
    if user.get("avatar"):
        ext = "gif" if str(user["avatar"]).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.{ext}?size=256"
    try:
        index = (int(user["id"]) >> 22) % 6
    except (KeyError, ValueError):
        index = 0
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"
