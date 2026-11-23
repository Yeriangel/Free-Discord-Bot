"""Local control API for the UI. Listens on 127.0.0.1 only and requires a per-launch token."""

from .i18n import tr
import asyncio
import sys
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

import discord
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, catalog, discord_rest, i18n, secrets_store
from .bot import BotManager
from .bus import bus
from .store import Store, valid_snowflake

log = logging.getLogger("botd.api")
UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def err(status: int, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, **extra})


class SecretIn(BaseModel):
    value: str


class SettingsIn(BaseModel):
    ui_lang: str | None = None
    allowed_user_id: str | None = None
    allowed_channel_id: str | None = None
    keep_running_on_close: bool | None = None
    autostart_bot: bool | None = None
    start_with_windows: bool | None = None
    appearance: dict | None = None
    creator_anonymous: bool | None = None
    mobile_enabled: bool | None = None
    mobile_port: int | None = None


class ProfileIn(BaseModel):
    bio: str | None = None
    username: str | None = None
    avatar: str | None = None  # data: URI
    banner: str | None = None  # data: URI


class PresenceIn(BaseModel):
    status: str = "online"
    activity: str = "none"
    text: str = ""
    url: str = ""


class RepoIn(BaseModel):
    repo: str


class InstallIn(BaseModel):
    tag: str


class LangIn(BaseModel):
    language: str


class ConfigIn(BaseModel):
    config: dict


class RRPair(BaseModel):
    emoji: str = ""
    label: str = ""
    role_id: str


class ReactionRoleIn(BaseModel):
    channel_id: str
    content: str = ""
    title: str = ""
    color: str = ""
    style: str = "text"      # text | embed
    mode: str = "reactions"  # reactions | buttons
    pairs: list[RRPair]


class ModuleIn(BaseModel):
    enabled: bool


class InviteIn(BaseModel):
    permissions: list[str]
    guild_id: str = ""
    account: str = ""


def _require_token() -> str:
    token = secrets_store.get("bot_token")
    if not token:
        raise err(400, tr("לא הוגדר Bot Token"))
    return token


def _discord_error(exc: discord_rest.DiscordAPIError) -> HTTPException:
    status = exc.status if exc.status in (400, 401, 403, 404, 429) else 502
    return err(status, exc.message, retry_after=exc.retry_after)


def create_app(
    manager: BotManager,
    store: Store,
    session_token: str,
    port: int,
    request_shutdown: Callable[[], None],
) -> FastAPI:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    from . import build_id
    BUILD = build_id()  # the code this service was started with
    token_bytes = session_token.encode()

    def token_ok(value: str | None) -> bool:
        return hmac.compare_digest((value or "").encode("utf-8", "replace"), token_bytes)

    STANDBY_EVERY = int(__import__("os").environ.get("BOTD_STANDBY_EVERY") or 30)  # seconds between checks of the main computer
    STANDBY_MISSES = 3  # this many failed checks in a row = the main computer is off

    async def main_bot_online(url: str) -> bool | None:
        """True / False = what the main computer says about its bot; None = the main computer does not answer."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(f"{url}/api/ping")
            return bool(r.json().get("bot_online")) if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def standby_loop(url: str) -> None:
        """Backup mode: start the bot here when the main computer's bot is off or unreachable, and stop it as soon as
        the main computer's bot is back, so the same bot never answers twice for long."""
        misses = 0
        while True:
            online = await main_bot_online(url)
            running = manager.state in ("connecting", "connected", "reconnecting")
            misses = misses + 1 if online is None else 0
            main_off = online is False or misses >= STANDBY_MISSES
            try:
                if main_off and not running and secrets_store.get("bot_token"):
                    log.info(tr("הבוט במחשב הראשי כבוי: מפעיל את הבוט כאן"))
                    await manager.start()
                elif online and running:
                    log.info(tr("הבוט במחשב הראשי חזר: עוצר את הבוט כאן"))
                    await manager.stop()
            except Exception:  # noqa: BLE001 - keep watching
                log.exception("standby")
            await asyncio.sleep(STANDBY_EVERY)

    RESUME_KEY = "resume_bot_after_restart"
    RESUME_WINDOW = 90  # seconds: an update, restart or the launcher replacing an old version starts the next one at once

    def remember_running_bot() -> None:
        """When this service stops while the bot is online, the next service brings the bot back online by itself
        if it starts soon after (an update or restart), so nobody has to press Start again."""
        import time as _time

        if manager.state in ("connecting", "connected", "reconnecting"):
            store.set_kv(RESUME_KEY, _time.time())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        bus.attach(asyncio.get_running_loop())
        log.info(tr("שירות הבוט עלה (גרסה %s)"), __version__)
        watcher = asyncio.create_task(watch_for_replacement())
        import os

        autostart = store.get_settings()["autostart_bot"] or os.environ.get("BOTD_AUTOSTART") == "1"  # servers: always on
        import time as _time

        stamp = store.get_kv(RESUME_KEY, 0) or 0  # when the bot was last online at a shutdown
        resume = isinstance(stamp, (int, float)) and 0 < _time.time() - stamp < RESUME_WINDOW
        if stamp:
            store.set_kv(RESUME_KEY, 0)
        if resume:
            log.info(tr("הבוט רץ לפני העדכון, מפעיל אותו שוב"))
        standby_url = os.environ.get("BOTD_STANDBY_URL", "").rstrip("/")
        if standby_url:  # backup copy (a phone or second computer): the main computer decides, see standby_loop
            autostart = resume = False
            standby_task = asyncio.create_task(standby_loop(standby_url))
            log.info(tr("מצב גיבוי: הבוט יעבוד כאן רק כשהבוט במחשב הראשי כבוי (%s)"), standby_url)
        if (autostart or resume) and secrets_store.get("bot_token"):
            try:
                await manager.start()
            except ValueError as exc:
                log.warning("%s", exc)
        yield
        watcher.cancel()
        await manager.stop()
        log.info(tr("שירות הבוט נסגר"))

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    import os as _os

    from . import mobile as mobile_mod

    phones = mobile_mod.MobileAccess(store)
    phone_port = mobile_mod.mobile_port(store.get_settings()) if mobile_mod.enabled(store.get_settings()) else None
    allow_loopback_phone = _os.environ.get("BOTD_MOBILE_TEST") == "1"  # tests only

    def phone_client_ok(host: str | None) -> bool:
        return mobile_mod.is_tailscale(host) or (allow_loopback_phone and host == "127.0.0.1")

    def from_phone(scope) -> bool:
        server = scope.get("server") or (None, None)
        return phone_port is not None and server[1] == phone_port and phone_port != port

    PHONE_OPEN = ("/api/mobile/claim", "/api/ping")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if from_phone(request.scope):
            if not phone_client_ok(request.client.host if request.client else None):
                return JSONResponse({"detail": {"message": "only your Tailscale devices"}}, status_code=403)
            path = request.url.path
            if path.startswith("/api/"):
                if path.startswith(mobile_mod.BLOCKED_FOR_PHONES):
                    return JSONResponse({"detail": {"message": tr("את זה אפשר לעשות רק במחשב")}}, status_code=403)
                if path not in PHONE_OPEN:
                    device = phones.device_for(request.headers.get("x-session-token"))
                    if device is None:
                        return JSONResponse({"detail": {"message": "unauthorized"}}, status_code=401)
                    if not device.get("verified") and path != "/api/mobile/discord":
                        return JSONResponse({"detail": {"message": "discord login needed", "discord_login": True}}, status_code=401)
                    request.state.device = device
                    phones.touch(device["id"])
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            host = request.headers.get("host", "")
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
                f"connect-src 'self' ws://{host}; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            return response
        # Blocks DNS-rebinding and any non-local origin.
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"detail": {"message": "host not allowed"}}, status_code=403)
        if request.url.path.startswith("/api/") and not token_ok(request.headers.get("x-session-token")):
            return JSONResponse({"detail": {"message": "unauthorized"}}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    # ---- service ---------------------------------------------------------
    @app.get("/api/ping")
    async def ping():
        return {"ok": True, "version": __version__, "build": BUILD,
                "bot_online": manager.state in ("connecting", "connected", "reconnecting")}

    @app.post("/api/app/restart")
    async def restart_app():
        """Opens a fresh app window on the newest code: a new launcher replaces this service and the old window."""
        import subprocess
        import sys

        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) \
            if sys.platform == "win32" else 0
        remember_running_bot()
        subprocess.Popen([str(pythonw if pythonw.exists() else exe), str(UI_DIR.parent / "app.py"), "--restart"],
                         cwd=str(UI_DIR.parent), creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
        log.info(tr("האפליקציה נפתחת מחדש…"))
        return {"ok": True}

    @app.post("/api/app/reload")
    async def reload_service():
        """Applies an installed update without closing the window: a new service (the new code) takes over this
        port and token, the bots reconnect, and the page reloads itself. Big updates use /api/app/restart instead."""
        import os
        import subprocess
        import sys

        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) \
            if sys.platform == "win32" else 0
        env = dict(os.environ, BOTD_TAKEOVER="1", BOTD_PORT=str(port), BOTD_TOKEN=session_token)
        from . import paths

        subprocess.Popen([str(pythonw if pythonw.exists() else exe), "-m", "botd"], cwd=str(UI_DIR.parent), env=env,
                         creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=open(paths.log_dir() / "botd-stderr.log", "ab"), close_fds=True)
        remember_running_bot()
        asyncio.create_task(_shutdown_now(tr("מחיל את העדכון: השירות נטען מחדש עם הגרסה החדשה")))
        return {"ok": True}

    async def _shutdown_now(reason: str) -> None:
        """Disconnect every bot first (so a slow exit can never leave a bot answering), then stop the service.
        If the process is somehow still alive 15 seconds later, it ends for sure."""
        import os

        log.info(reason)
        loop = asyncio.get_running_loop()
        loop.call_later(15, os._exit, 0)
        try:
            await asyncio.wait_for(manager.stop(), timeout=10)
        except Exception:  # noqa: BLE001
            log.exception("stopping the bots failed")
        loop.call_later(0.3, request_shutdown)

    @app.post("/api/shutdown")
    async def shutdown():
        remember_running_bot()
        asyncio.create_task(_shutdown_now(tr("התקבלה בקשת כיבוי לשירות")))
        return {"ok": True}

    async def watch_for_replacement() -> None:
        """A newer service took over (its details are in session.json): stop this one, so two copies of the same
        bot never answer in Discord at the same time."""
        import os

        from .__main__ import read_session, session_alive

        await asyncio.sleep(10)
        while True:
            await asyncio.sleep(5)
            current = await asyncio.to_thread(read_session)
            if current and current.get("pid") not in (None, os.getpid()) and current.get("token") != session_token \
                    and await asyncio.to_thread(session_alive, current):
                remember_running_bot()
                await _shutdown_now(tr("שירות חדש יותר פועל, השירות הישן נסגר"))
                return

    # ---- bot lifecycle ---------------------------------------------------
    @app.get("/api/status")
    async def status():
        return manager.status()

    @app.post("/api/bot/start")
    async def bot_start():
        try:
            await manager.start()
        except ValueError as exc:
            raise err(400, str(exc))
        return manager.status()

    @app.post("/api/bot/stop")
    async def bot_stop():
        await manager.stop()
        return manager.status()

    @app.post("/api/intents/enable")
    async def intents_enable():
        try:
            found = await manager.enable_intents()
        except ValueError as exc:
            raise err(400, str(exc))
        except discord_rest.DiscordAPIError as exc:
            raise err(400, tr("דיסקורד לא איפשר להפעיל אוטומטית ({error}). הפעל ידנית ב-Developer Portal, בלשונית Bot.",
                              error=exc.message), portal=True)
        if not all(found.values()):
            raise err(400, tr("דיסקורד לא איפשר להפעיל אוטומטית. בוטים ביותר מ-100 שרתים צריכים אישור מדיסקורד. הפעל ידנית ב-Developer Portal, בלשונית Bot."),
                      portal=True)
        return manager.status()

    @app.post("/api/backup")
    async def make_backup():
        """A zip with all settings and data (no keys), for moving the bot to a server. Saved in Downloads."""
        import datetime as dt
        import os
        import sqlite3
        import tempfile
        import zipfile

        from . import paths

        folder = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"
        if not folder.is_dir():
            folder = paths.data_dir()
        target = folder / f"FreeDiscordBot-backup-{dt.datetime.now():%Y-%m-%d-%H%M}.zip"
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "botd.sqlite3"
            src = sqlite3.connect(paths.db_file())
            dst = sqlite3.connect(copy)
            with dst:
                src.backup(dst)  # a consistent copy even while the bot is running
            src.close()
            dst.close()
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(copy, "botd.sqlite3")
                zf.writestr("README.txt", "Free Discord Bot backup: settings and data of every server. No keys or tokens.\n"
                                          "On the server: python -m botd import <this file>  (see HOSTING.md)\n")
        log.info(tr("נשמר גיבוי של ההגדרות: %s"), target)
        if sys.platform == "win32" and os.environ.get("BOTD_REVEAL", "1") != "0":  # show the file in Explorer
            import subprocess

            subprocess.Popen(["explorer", "/select,", str(target)])
        return {"path": str(target)}

    @app.post("/api/user-install")
    async def user_install():
        """Turns on "Add App" (user install) for the bot and returns the link people use to add it."""
        from . import secrets_store

        token = secrets_store.get("bot_token")
        if not token:
            raise err(400, tr("לא הוגדר Bot Token. הוסף אותו בעמוד 'חיבור והגדרות'."))
        try:
            result = await discord_rest.enable_user_install(token)
        except discord_rest.DiscordAPIError as exc:
            raise err(400, tr("דיסקורד לא איפשר להפעיל אוטומטית ({error}). הפעל ידנית ב-Developer Portal, בלשונית Installation.",
                              error=exc.message), portal=True)
        if not result["enabled"]:
            raise err(400, tr("דיסקורד לא איפשר להפעיל אוטומטית. הפעל ידנית ב-Developer Portal, בלשונית Installation (User Install)."),
                      portal=True)
        log.info(tr("התקנה לחשבון (Add App) הופעלה לבוט"))
        return result

    # ---- settings & secrets ---------------------------------------------
    @app.get("/api/settings")
    async def get_settings():
        from . import autostart

        return {
            "start_with_windows": autostart.is_enabled(),
            "secrets": {name: secrets_store.describe(name) for name in secrets_store.NAMES},
            "settings": store.get_settings(),
        }

    @app.put("/api/settings")
    async def put_settings(body: SettingsIn):
        old_lang = store.get_settings()["ui_lang"]
        changes = body.model_dump(exclude_none=True)
        if "start_with_windows" in changes:
            from . import autostart

            on = bool(changes.pop("start_with_windows"))
            try:
                autostart.set_enabled(on)
            except (OSError, ValueError) as exc:
                raise err(400, str(exc))
            if on:  # starting with Windows only helps if the bot also connects by itself
                changes["autostart_bot"] = True
            log.info(tr("הפעלה עם Windows: %s"), "✓" if on else "✕")
        try:
            store.update_settings(changes)
        except ValueError as exc:
            raise err(400, str(exc))
        if body.ui_lang and body.ui_lang != old_lang:
            # one language for everything: the app, the bot's messages in every server and the slash commands
            for g in store.list_guilds():
                store.set_guild_lang(g["id"], body.ui_lang)
            log.info(tr("השפה שונתה ל-%s באפליקציה ובכל השרתים"), i18n.LANGS[body.ui_lang]["name"])
            managers = manager.managers() if hasattr(manager, "managers") else [manager]
            for m in managers:
                if m.bot is not None and m.state == "connected":
                    asyncio.create_task(m.bot._sync_commands())
        return await get_settings()

    @app.put("/api/secrets/{name}")
    async def put_secret(name: str, body: SecretIn):
        if name not in secrets_store.NAMES:
            raise err(404, tr("סוד לא מוכר"))
        if len(body.value) > 500:
            raise err(400, tr("ארוך מדי"))
        try:
            secrets_store.set(name, body.value)
        except ValueError as exc:
            raise err(400, str(exc))
        log.info(tr("הסוד %s עודכן"), name)
        return secrets_store.describe(name)

    @app.delete("/api/secrets/{name}")
    async def delete_secret(name: str):
        if name not in secrets_store.NAMES:
            raise err(404, tr("סוד לא מוכר"))
        secrets_store.delete(name)
        log.info(tr("הסוד %s נמחק"), name)
        return secrets_store.describe(name)

    @app.post("/api/secrets/{name}/test")
    async def test_secret(name: str):
        if name == "bot_token":
            try:
                me = await discord_rest.get_me(_require_token())
            except discord_rest.DiscordAPIError as exc:
                raise _discord_error(exc)
            return {"ok": True, "message": tr("הטוקן תקין: הבוט {name}", name=me.get('username'))}
        if name == "anthropic_key":
            key = secrets_store.get("anthropic_key")
            if not key:
                raise err(400, tr("לא הוגדר Anthropic API Key"))
            import anthropic

            try:
                async with anthropic.AsyncAnthropic(api_key=key) as client:
                    await client.models.list(limit=1)
            except anthropic.AuthenticationError:
                raise err(401, tr("המפתח לא תקין"))
            except anthropic.APIError as exc:
                raise err(502, tr("Anthropic החזיר שגיאה: {error}", error=type(exc).__name__))
            return {"ok": True, "message": tr("המפתח תקין")}
        if name in ("openai_key", "xai_key"):
            from .modules import ai_providers

            provider = "openai" if name == "openai_key" else "grok"
            key = secrets_store.get(name)
            if not key:
                raise err(400, tr("לא הוגדר מפתח"))
            try:
                ok = await ai_providers.test_key(provider, key)
            except Exception as exc:  # noqa: BLE001
                raise err(502, tr("{name} החזיר שגיאה: {error}", name=ai_providers.PROVIDERS[provider]["name"], error=type(exc).__name__))
            if not ok:
                raise err(401, tr("המפתח לא תקין"))
            return {"ok": True, "message": tr("המפתח תקין")}
        if name in ("twitch_client_id", "twitch_client_secret"):
            import httpx

            cid, secret = secrets_store.get("twitch_client_id"), secrets_store.get("twitch_client_secret")
            if not cid or not secret:
                raise err(400, tr("צריך גם Client ID וגם Client Secret"))
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post("https://id.twitch.tv/oauth2/token", data={
                    "client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
            if resp.status_code != 200:
                raise err(401, tr("Twitch דחה את הפרטים"))
            return {"ok": True, "message": tr("הפרטים של Twitch תקינים")}
        if name == "roblox_api_key":
            import httpx

            key = secrets_store.get("roblox_api_key")
            if not key:
                raise err(400, tr("לא הוגדר מפתח"))
            async with httpx.AsyncClient(timeout=15) as client:  # asset 1818 is a public Roblox place
                resp = await client.get("https://apis.roblox.com/asset-delivery-api/v1/assetId/1818", headers={"x-api-key": key})
            if resp.status_code in (401, 403):
                raise err(401, tr("רובלוקס דחה את המפתח. בדוק שיש לו הרשאת legacy-asset:manage."))
            if resp.status_code != 200:
                raise err(502, tr("רובלוקס החזיר שגיאה: {error}", error=resp.status_code))
            return {"ok": True, "message": tr("המפתח תקין")}
        raise err(404, tr("סוד לא מוכר"))

    # ---- bot profile (name / avatar) ------------------------------------
    @app.get("/api/profile")
    async def get_profile():
        try:
            me = await discord_rest.get_me(_require_token())
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)
        return await _profile_view(me)

    async def _profile_view(me: dict, bio: str | None = None) -> dict:
        if bio is None:
            try:
                bio = (await discord_rest.get_application(_require_token())).get("description") or ""
            except discord_rest.DiscordAPIError:
                bio = ""
        return {"id": me["id"], "username": me.get("username", ""), "discriminator": me.get("discriminator", "0"),
                "avatar_url": discord_rest.avatar_url(me), "banner_url": discord_rest.banner_url(me),
                "presence": store.get_settings()["presence"], "bio": bio}

    @app.put("/api/presence")
    async def put_presence(body: PresenceIn):
        try:
            store.update_settings({"presence": body.model_dump()})
        except ValueError as exc:
            raise err(400, str(exc))
        await manager.apply_presence()
        log.info(tr("הסטטוס של הבוט עודכן"))
        return store.get_settings()["presence"]

    @app.patch("/api/profile")
    async def patch_profile(body: ProfileIn):
        bio = None
        try:
            if body.bio is not None:
                bio = await discord_rest.update_bio(_require_token(), body.bio)
            if body.username or body.avatar or body.banner:
                me = await discord_rest.update_me(_require_token(), body.username, body.avatar, body.banner)
            else:
                me = await discord_rest.get_me(_require_token())
        except ValueError as exc:
            raise err(400, str(exc))
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)
        changed = [k for k in ("username", "avatar", "banner") if getattr(body, k)] + (["bio"] if body.bio is not None else [])
        log.info(tr("פרופיל הבוט עודכן (%s)"), ", ".join(changed))
        return await _profile_view(me, bio)

    # ---- catalog, permissions, invite -----------------------------------
    @app.get("/api/i18n")
    async def get_i18n():
        lang = i18n.ui_lang()
        return {"lang": lang, "dir": i18n.LANGS[lang]["dir"], "strings": i18n.ui_strings(lang),
                "languages": [{"code": k, **v} for k, v in i18n.LANGS.items()]}

    @app.get("/api/catalog")
    async def get_catalog():
        data = i18n.translate_catalog(catalog.catalog_json(), i18n.ui_lang())
        data["recommended_permissions"] = catalog.recommended_permissions(store.enabled_modules_anywhere())
        return data

    @app.post("/api/invite")
    async def make_invite(body: InviteIn):
        if not valid_snowflake(body.guild_id):
            raise err(400, tr("מזהה שרת לא תקין"))
        try:
            acc = manager.get(body.account) if hasattr(manager, "get") and body.account else None
            token = secrets_store.get(acc.token_name) if acc is not None else _require_token()
            if not token:
                raise err(400, tr("לא הוגדר Bot Token"))
            app_info = await discord_rest.get_application(token)
            url = catalog.invite_url(str(app_info["id"]), body.permissions, body.guild_id)
        except ValueError as exc:
            raise err(400, str(exc))
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)
        return {"url": url, "permissions_value": str(catalog.permissions_value(body.permissions))}

    # ---- guilds ----------------------------------------------------------
    # ---- phone access (Android app) -------------------------------------------------------------
    def _phone_base_url() -> str | None:
        ip = _os.environ.get("BOTD_MOBILE_HOST") or mobile_mod.tailscale_ip()
        settings = store.get_settings()
        return f"http://{ip}:{mobile_mod.mobile_port(settings)}" if ip else None

    @app.get("/api/mobile")
    async def mobile_info():
        settings = store.get_settings()
        return {"enabled": mobile_mod.enabled(settings), "running": phone_port is not None,
                "port": mobile_mod.mobile_port(settings), "tailscale_ip": await asyncio.to_thread(mobile_mod.tailscale_ip),
                "devices": phones.public_devices(), "discord_redirect": mobile_mod.DISCORD_REDIRECT}

    @app.post("/api/mobile/pair")
    async def mobile_pair():
        """A pairing code + QR for the phone (valid 10 minutes)."""
        import base64

        from . import qr

        if phone_port is None:
            raise err(409, tr("קודם הפעל גישה מהטלפון ולחץ 'החל עכשיו'."))
        base = await asyncio.to_thread(_phone_base_url)
        if not base:
            raise err(409, tr("Tailscale לא פועל במחשב הזה. התקן אותו והתחבר, ואז נסה שוב."))
        code = phones.new_code()
        link = mobile_mod.pair_link(base, code)
        png = qr.to_png(qr.encode(link), scale=6)
        return {"code": code, "url": base, "link": link, "qr": "data:image/png;base64," + base64.b64encode(png).decode(),
                "expires_in": mobile_mod.PAIR_CODE_SECONDS}

    @app.delete("/api/mobile/devices/{device_id}")
    async def mobile_remove(device_id: str):
        if not phones.remove(device_id):
            raise err(404, tr("הטלפון לא נמצא"))
        log.info(tr("טלפון הוסר"))
        return {"devices": phones.public_devices()}

    @app.post("/api/mobile/claim")
    async def mobile_claim(body: dict):
        try:
            token = phones.claim(str(body.get("code") or ""), str(body.get("name") or "Phone"))
        except ValueError as exc:
            raise err(400, str(exc))
        return {"token": token, "client_id": await _client_id() if secrets_store.get("bot_token") else "",
                "discord_redirect": mobile_mod.DISCORD_REDIRECT}

    @app.post("/api/mobile/discord")
    async def mobile_discord(request: Request, body: dict):
        """The phone logged in with Discord: allowed only for the bot's owner or an allowed user."""
        import httpx

        from .store import allowed_user_ids

        device = getattr(request.state, "device", None)
        if device is None:
            raise err(401, "unauthorized")
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://discord.com/api/v10/users/@me",
                                 headers={"Authorization": f"Bearer {body.get('access_token') or ''}"})
        if r.status_code != 200:
            raise err(401, tr("ההתחברות לדיסקורד נכשלה. נסה שוב."))
        allowed = set(allowed_user_ids(store.get_settings()))
        bot = manager.bot
        if bot is not None and getattr(bot, "creator_id", None):
            allowed.add(str(bot.creator_id))
        try:
            phones.verify(device["id"], r.json(), allowed)
        except PermissionError as exc:
            raise err(403, str(exc))
        return {"ok": True}

    # ---- Home panel: log in with Discord, see your servers ------------------------------------
    login_flow = None

    async def _client_id() -> str:
        if getattr(manager, "app_id", ""):
            return manager.app_id
        try:
            return str((await discord_rest.get_application(_require_token())).get("id") or "")
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)

    def _open_in_browser(url: str) -> None:
        import webbrowser
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not (host == "discord.com" or host.endswith(".discord.com")):
            raise err(400, tr("אפשר לפתוח רק קישורים של דיסקורד"))
        webbrowser.open(url)

    @app.get("/api/home")
    async def get_home():
        from . import discord_login

        token = discord_login.load_login()
        base = {"redirect_uri": discord_login.REDIRECT_URI, "client_id": await _client_id() if secrets_store.get("bot_token") else ""}
        if not token:
            return {"logged_in": False, **base}
        bot_ids = {g["id"] for g in store.list_guilds()}
        live = manager.live_guild_ids()
        if live:
            bot_ids = {str(i) for i in live}
        try:
            data = await discord_login.home(token, bot_ids)
        except PermissionError:
            return {"logged_in": False, "expired": True, **base}
        except ValueError as exc:
            raise err(502, str(exc))
        return {"logged_in": True, **base, **data}

    @app.post("/api/home/login")
    async def home_login():
        """Opens Discord's login in the normal browser; the app checks /api/home until it is done."""
        nonlocal login_flow
        from . import discord_login

        client_id = await _client_id()
        if not client_id:
            raise err(400, tr("לא הוגדר Bot Token. הוסף אותו בעמוד 'חיבור והגדרות'."))
        login_flow = login_flow or discord_login.LoginFlow()
        try:
            url = await login_flow.start(client_id)
        except ValueError as exc:
            raise err(409, str(exc))
        _open_in_browser(url)
        return {"url": url}

    @app.post("/api/home/logout")
    async def home_logout():
        from . import discord_login

        discord_login.logout()
        log.info(tr("התנתקת מדיסקורד בפאנל הבית"))
        return {"ok": True}

    @app.post("/api/open-url")
    async def open_url(body: dict):
        _open_in_browser(str(body.get("url") or ""))
        return {"ok": True}

    @app.get("/api/guilds")
    async def list_guilds():
        live = manager.live_guild_ids()
        guilds = store.list_guilds()
        for g in guilds:
            g["present"] = g["id"] in live if live else None
        return guilds

    def _guild_or_404(gid: str) -> dict:
        guild = store.get_guild(gid) if valid_snowflake(gid) and gid else None
        if guild is None:
            raise err(404, tr("השרת לא נמצא"))
        return guild

    def _guild_view(gid: str) -> dict:
        guild = _guild_or_404(gid)
        lang = store.guild_lang(gid)
        guild["language"] = lang
        guild["config"] = {k: catalog.merged_config(k, guild["config"].get(k), lang) for k in catalog.MODULE_KEYS}
        guild["objects"] = manager.guild_objects(gid)
        live = manager.live_guild_ids()
        guild["present"] = gid in live if live else None
        return guild

    @app.get("/api/guilds/{gid}")
    async def get_guild(gid: str):
        return _guild_view(gid)

    @app.put("/api/guilds/{gid}/language")
    async def put_guild_language(gid: str, body: LangIn):
        guild = _guild_or_404(gid)
        if not i18n.valid_lang(body.language):
            raise err(400, tr("שפה לא נתמכת"))
        store.set_guild_lang(gid, body.language)
        log.info(tr("שפת הבוט בשרת %s שונתה ל-%s"), guild["name"], i18n.LANGS[body.language]["name"])
        return _guild_view(gid)

    @app.put("/api/guilds/{gid}/modules/{key}/config")
    async def put_module_config(gid: str, key: str, body: ConfigIn):
        guild = _guild_or_404(gid)
        if key not in catalog.MODULE_KEYS:
            raise err(404, tr("מודול לא מוכר"))
        current = catalog.merged_config(key, guild["config"].get(key), store.guild_lang(gid))
        try:
            clean = catalog.validate_config(key, body.config, current)
        except ValueError as exc:
            raise err(400, str(exc))
        if key == "custom_commands":  # a slash custom command must not take the name of one of the bot's commands
            from .bot import command_specs

            custom = store.get_kv("command_custom", {}) or {}
            taken = {(custom.get(c["name"]) or {}).get("name") or c["name"] for c in await command_specs(store)}
            clash = [c["name"] for c in clean["commands"] if c["kind"] in ("slash", "both") and c["name"] in taken]
            if clash:
                raise err(400, tr("כבר יש לבוט פקודת סלאש בשם /{name}. בחר שם אחר.", name=clash[0]))
        store.set_config(gid, key, catalog.strip_defaults(key, clean))
        log.info(tr("ההגדרות של %s עודכנו בשרת %s"), key, guild["name"])
        if key == "custom_commands":
            _resync_custom_commands(gid)
        return _guild_view(gid)

    def _rr_cog(gid: str = ""):
        bot = manager.bot_for(gid) if gid and hasattr(manager, "bot_for") else manager.bot
        cog = bot.get_cog("ReactionRoles") if bot is not None else None
        if cog is None:
            raise err(409, tr("הבוט צריך להיות מחובר כדי לפרסם הודעה בדיסקורד"))
        return cog

    @app.post("/api/guilds/{gid}/reaction-roles")
    async def add_reaction_roles(gid: str, body: ReactionRoleIn):
        _guild_or_404(gid)
        cog = _rr_cog(gid)
        guild = manager.live_guild(gid)
        if guild is None:
            raise err(409, tr("הבוט לא נמצא בשרת הזה"))
        data = body.model_dump()
        try:
            await cog.publish(guild, data.pop("channel_id"), **data)
        except ValueError as exc:
            raise err(400, str(exc))
        except discord.HTTPException as exc:
            raise err(502, tr("דיסקורד דחה את הבקשה: {message}", message=exc.text or exc.status))
        return _guild_view(gid)

    @app.put("/api/guilds/{gid}/reaction-roles/{message_id}")
    async def edit_reaction_roles(gid: str, message_id: str, body: ReactionRoleIn):
        """Edits a posted reaction-roles group, and its message in Discord."""
        _guild_or_404(gid)
        cog = _rr_cog(gid)
        guild = manager.live_guild(gid)
        if guild is None:
            raise err(409, tr("הבוט לא נמצא בשרת הזה"))
        data = body.model_dump()
        try:
            await cog.update(guild, message_id, data.pop("channel_id"), data)
        except KeyError:
            raise err(404, tr("ההודעה לא נמצאה"))
        except ValueError as exc:
            raise err(400, str(exc))
        except discord.HTTPException as exc:
            raise err(502, tr("דיסקורד דחה את הבקשה: {message}", message=exc.text or exc.status))
        return _guild_view(gid)

    @app.delete("/api/guilds/{gid}/reaction-roles/{message_id}")
    async def delete_reaction_roles(gid: str, message_id: str, delete_message: bool = False):
        _guild_or_404(gid)
        groups = catalog.merged_config("reaction_roles", store.raw_config(gid, "reaction_roles"))["groups"]
        if not any(g.get("message_id") == message_id for g in groups):
            raise err(404, tr("ההודעה לא נמצאה"))
        cog = manager.bot.get_cog("ReactionRoles") if manager.bot is not None else None
        if delete_message:
            cog = _rr_cog(gid)
        if cog is not None:
            await cog.remove(gid, message_id, delete_message)
        else:
            store.set_config(gid, "reaction_roles", {"groups": [g for g in groups if g.get("message_id") != message_id]})
        return _guild_view(gid)

    @app.put("/api/guilds/{gid}/modules/{key}")
    async def put_module(gid: str, key: str, body: ModuleIn):
        _guild_or_404(gid)
        if key not in catalog.MODULE_KEYS:
            raise err(404, tr("מודול לא מוכר"))
        guild = store.set_module(gid, key, body.enabled)
        log.info(tr("המודול %s הופעל בשרת %s") if body.enabled else tr("המודול %s כובה בשרת %s"), key, guild["name"])
        if key == "custom_commands":  # slash custom commands appear / disappear with the module
            _resync_custom_commands(gid)
        return _guild_view(gid)

    def _resync_custom_commands(gid: str) -> None:
        bot = manager.bot_for(gid) if hasattr(manager, "bot_for") else manager.bot
        cog = bot.get_cog("CustomCommands") if bot is not None else None
        live = manager.live_guild(gid)
        if cog is not None and live is not None:
            asyncio.create_task(cog.sync_guild(live))

    @app.delete("/api/guilds/{gid}")
    async def forget_guild(gid: str):
        _guild_or_404(gid)
        store.delete_guild(gid)
        return {"ok": True}

    # ---- warnings -------------------------------------------------------------
    @app.get("/api/guilds/{gid}/warnings")
    async def list_warnings(gid: str, user_id: str = ""):
        _guild_or_404(gid)
        if not valid_snowflake(user_id):
            raise err(400, tr("מזהה משתמש לא תקין"))
        return store.list_warnings(gid, user_id or None, limit=200)

    @app.delete("/api/guilds/{gid}/warnings/{wid}")
    async def delete_warning(gid: str, wid: int):
        guild = _guild_or_404(gid)
        if not store.delete_warning(gid, wid):
            raise err(404, tr("האזהרה לא נמצאה"))
        log.info(tr("אזהרה #%d נמחקה מהאפליקציה בשרת %s"), wid, guild["name"])
        return {"ok": True}

    # ---- levels -----------------------------------------------------------------
    @app.get("/api/guilds/{gid}/levels")
    async def levels_board(gid: str, limit: int = 50):
        from .modules.levels import level_info

        _guild_or_404(gid)
        rows = store.leaderboard(gid, limit=max(1, min(limit, 200)))
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
            r["level"], r["into"], r["needed"] = level_info(r["xp"])
        return rows

    @app.delete("/api/guilds/{gid}/levels/{uid}")
    async def levels_reset(gid: str, uid: str):
        guild = _guild_or_404(gid)
        if not valid_snowflake(uid) or not uid or not store.reset_xp(gid, uid):
            raise err(404, tr("המשתמש לא נמצא בלוח"))
        log.info(tr("ה-XP של משתמש %s אופס מהאפליקציה בשרת %s"), uid, guild["name"])
        return {"ok": True}

    # ---- economy ----------------------------------------------------------------
    @app.get("/api/guilds/{gid}/economy")
    async def economy_board(gid: str, limit: int = 50):
        _guild_or_404(gid)
        rows = store.rich_list(gid, limit=max(1, min(limit, 200)))
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
            r["items"] = sum(x["qty"] for x in store.inventory(gid, r["user_id"]))
        return rows

    @app.delete("/api/guilds/{gid}/economy/{uid}")
    async def economy_reset(gid: str, uid: str):
        guild = _guild_or_404(gid)
        if not valid_snowflake(uid) or not uid or not store.reset_wallet(gid, uid):
            raise err(404, tr("המשתמש לא נמצא"))
        log.info(tr("הארנק של משתמש %s אופס מהאפליקציה בשרת %s"), uid, guild["name"])
        return {"ok": True}

    # ---- customization: bot texts, colors, emojis (per server) and slash commands (all servers) ----
    @app.get("/api/guilds/{gid}/custom")
    async def get_custom(gid: str):
        from . import customize
        from .modules.common import EMOJIS, PALETTE

        _guild_or_404(gid)
        return {**store.guild_custom(gid), "catalog": customize.text_catalog(store.guild_lang(gid)),
                "palette": {k: f"#{v:06X}" for k, v in PALETTE.items()}, "emoji_defaults": EMOJIS}

    @app.put("/api/guilds/{gid}/custom")
    async def put_custom(gid: str, body: ConfigIn):
        from . import customize

        guild = _guild_or_404(gid)
        try:
            clean = customize.validate_custom(body.config)
        except ValueError as exc:
            raise err(400, str(exc))
        store.set_config(gid, "_custom", clean)
        log.info(tr("ההתאמות האישיות נשמרו בשרת %s (%d טקסטים)"), guild["name"], len(clean["texts"]))
        return clean

    @app.get("/api/commands")
    async def get_commands():
        from .bot import command_specs

        specs = await command_specs(store)
        return {"commands": specs, "custom": store.get_kv("command_custom", {}) or {}}

    @app.put("/api/commands")
    async def put_commands(body: ConfigIn):
        from . import customize
        from .bot import command_specs

        known = {c["name"] for c in await command_specs(store)}
        try:
            clean = customize.validate_commands(body.config, known)
        except ValueError as exc:
            raise err(400, str(exc))
        store.set_kv("command_custom", clean)
        restarted = False
        if manager.task is not None and not manager.task.done():
            await manager.stop()
            await manager.start()
            restarted = True
        log.info(tr("הגדרות פקודות הסלאש נשמרו (%d שינויים)"), len(clean))
        return {"custom": clean, "restarted": restarted}

    # ---- console: typed commands and talking as the bot -------------------------------
    class ConsoleIn(BaseModel):
        line: str

    class SendIn(BaseModel):
        content: str

    @app.post("/api/console")
    async def console(body: ConsoleIn):
        from .console import run_command

        return {"lines": await run_command(manager, store, body.line)}

    @app.get("/api/guilds/{gid}/channels/{cid}/messages")
    async def channel_messages(gid: str, cid: str, limit: int = 40):
        guild = _live(gid)
        channel = guild.get_channel(int(cid)) if cid.isdigit() else None
        if channel is None or not hasattr(channel, "history"):
            raise err(404, tr("לא נמצא הערוץ"))
        try:
            msgs = [m async for m in channel.history(limit=max(1, min(limit, 100)))]
        except discord.Forbidden:
            raise err(403, tr("לבוט אין הרשאה לקרוא את הערוץ הזה"))
        return [{"id": str(m.id), "author": m.author.display_name, "author_id": str(m.author.id), "bot": m.author.bot,
                 "avatar": m.author.display_avatar.with_size(64).url, "content": m.content,
                 "embeds": len(m.embeds), "attachments": [a.url for a in m.attachments][:4],
                 "time": m.created_at.timestamp()} for m in reversed(msgs)]

    @app.post("/api/guilds/{gid}/channels/{cid}/messages")
    async def channel_send(gid: str, cid: str, body: SendIn):
        guild = _live(gid)
        channel = guild.get_channel(int(cid)) if cid.isdigit() else None
        text = body.content.strip()
        if channel is None or not hasattr(channel, "send"):
            raise err(404, tr("לא נמצא הערוץ"))
        if not text or len(text) > 2000:
            raise err(400, tr("ההודעה ריקה או ארוכה מ-2000 תווים"))
        try:
            msg = await channel.send(text, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
        except discord.HTTPException as exc:
            raise err(400, tr("דיסקורד דחה את ההודעה: {error}", error=exc.text or exc.status))
        log.info(tr("נשלחה הודעה מהקונסול ל-#%s בשרת %s"), channel.name, guild.name)
        return {"id": str(msg.id)}

    # ---- newer modules: actions and data views -------------------------------------
    def _cog(gid: str, name: str):
        """A module of the bot account that runs this server."""
        bot = manager.bot_for(gid) if hasattr(manager, "bot_for") else manager.bot
        return bot.get_cog(name) if bot is not None else None

    def _live(gid: str) -> discord.Guild:
        _guild_or_404(gid)
        guild = manager.live_guild(gid)
        if guild is None:
            raise err(409, tr("הבוט צריך להיות מחובר ובשרת הזה"))
        return guild

    @app.post("/api/guilds/{gid}/embed/send")
    async def send_embed(gid: str):
        from .modules.embeds import build_embed

        guild = _live(gid)
        cfg = catalog.merged_config("embed_messages", store.raw_config(gid, "embed_messages"))
        channel = guild.get_channel(int(cfg["channel_id"])) if cfg["channel_id"] else None
        if channel is None or not hasattr(channel, "send"):
            raise err(400, tr("בחר ערוץ ושמור"))
        try:
            msg = await channel.send(content=(cfg.get("content") or "").strip()[:2000] or None, embed=build_embed(cfg),
                                     allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True))
        except ValueError as exc:
            raise err(400, str(exc))
        except discord.HTTPException as exc:
            raise err(400, tr("דיסקורד דחה את ההודעה: {error}", error=exc.text or exc.status))
        log.info(tr("נשלחה הודעת Embed ל-#%s בשרת %s"), channel.name, guild.name)
        return {"ok": True, "url": msg.jump_url}

    class ChannelIn(BaseModel):
        name: str
        kind: str = "text"

    @app.post("/api/guilds/{gid}/channels")
    async def create_channel(gid: str, body: ChannelIn):
        guild = _live(gid)
        name = body.name.strip()[:100]
        if not name:
            raise err(400, tr("צריך שם לערוץ"))
        if not guild.me.guild_permissions.manage_channels:
            raise err(403, tr("לבוט אין הרשאת 'ניהול ערוצים' בשרת הזה"))
        try:
            if body.kind == "voice":
                ch = await guild.create_voice_channel(name, reason="created from the app")
            else:
                ch = await guild.create_text_channel(name, reason="created from the app")
        except discord.HTTPException as exc:
            raise err(400, tr("דיסקורד דחה את הבקשה: {message}", message=exc.text or exc.status))
        log.info(tr("נוצר ערוץ %s בשרת %s"), ch.name, guild.name)
        return {"id": str(ch.id), "name": ch.name}

    @app.post("/api/guilds/{gid}/ticket-panel")
    async def ticket_panel(gid: str):
        guild = _live(gid)
        cog = _cog(gid, "Ticketing")
        try:
            msg = await cog.publish_panel(guild)
        except ValueError as exc:
            raise err(400, str(exc))
        except discord.HTTPException as exc:
            raise err(400, tr("דיסקורד דחה את ההודעה: {error}", error=exc.text or exc.status))
        return {"ok": True, "url": msg.jump_url}

    # ---- bot accounts (several tokens) and the bot's profile in each server -------------------
    class AccountIn(BaseModel):
        label: str = ""
        token: str

    class GuildBotIn(BaseModel):
        account: str = ""

    class MemberProfileIn(BaseModel):
        nick: str | None = None
        avatar: str | None = None
        banner: str | None = None
        bio: str | None = None
        reset: list[str] = []

    @app.get("/api/accounts")
    async def list_accounts():
        return manager.accounts_status()

    @app.post("/api/accounts")
    async def add_account(body: AccountIn):
        token = body.token.strip()
        try:
            me = await discord_rest.get_me(token)
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)
        for m in manager.managers():  # the same bot twice would answer twice
            existing = secrets_store.get(m.token_name)
            if existing and (existing == token or (m.bot and m.bot.user and str(m.bot.user.id) == str(me.get("id")))):
                raise err(400, tr("הבוט הזה כבר מחובר לאפליקציה"))
        acc = manager.add_account(body.label.strip() or me.get("username", ""))
        secrets_store.set(acc.token_name, token)
        log.info(tr("נוסף חשבון בוט: %s"), me.get("username"))
        if manager.main.task is not None and not manager.main.task.done():
            try:
                await acc.start()
            except ValueError as exc:
                log.warning("%s", exc)
        return manager.accounts_status()

    @app.delete("/api/accounts/{account_id}")
    async def delete_account(account_id: str):
        if account_id == "main" or account_id not in manager.extras:
            raise err(404, tr("לא נמצא"))
        await manager.remove_account(account_id)
        for g in store.list_guilds():
            if store.guild_bot(g["id"]) == account_id:
                store.set_guild_bot(g["id"], "")
        return manager.accounts_status()

    @app.put("/api/guilds/{gid}/bot")
    async def set_guild_bot(gid: str, body: GuildBotIn):
        _guild_or_404(gid)
        if body.account and manager.get(body.account) is None:
            raise err(404, tr("לא נמצא"))
        store.set_guild_bot(gid, body.account if body.account != "main" else "main")
        return _guild_view(gid)

    @app.get("/api/guilds/{gid}/member-profile")
    async def get_member_profile(gid: str):
        _guild_or_404(gid)
        owner = manager.owner_of(gid)
        saved = (store.get_guild(gid) or {}).get("config", {}).get("_profile") or {}
        out = {"account": owner.account_id if owner else None, "nick": saved.get("nick", ""), "bio": saved.get("bio", ""),
               "avatar_url": saved.get("avatar_url", ""), "banner_url": saved.get("banner_url", ""),
               "default_name": "", "default_avatar": ""}
        if owner is not None and owner.bot is not None and owner.bot.user is not None:
            out["default_name"] = owner.bot.user.name
            out["default_avatar"] = owner.bot.user.display_avatar.with_size(128).url
            me = owner.bot.get_guild(int(gid)).me
            out["nick"] = me.nick or ""
        return out

    @app.put("/api/guilds/{gid}/member-profile")
    async def put_member_profile(gid: str, body: MemberProfileIn):
        guild = _guild_or_404(gid)
        owner = manager.owner_of(gid)
        if owner is None:
            raise err(409, tr("הבוט צריך להיות מחובר ובשרת הזה"))
        token = secrets_store.get(owner.token_name)
        try:
            member = await discord_rest.update_member_profile(token, gid, body.nick, body.avatar, body.banner, body.bio,
                                                              tuple(body.reset))
        except ValueError as exc:
            raise err(400, str(exc))
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)
        uid = str((member.get("user") or {}).get("id") or (owner.bot.user.id if owner.bot and owner.bot.user else ""))
        saved = {"nick": member.get("nick") or "", "bio": member.get("bio") or (body.bio or ""),
                 "avatar_url": discord_rest.member_asset_url(gid, uid, member, "avatar"),
                 "banner_url": discord_rest.member_asset_url(gid, uid, member, "banner")}
        if "bio" in body.reset:
            saved["bio"] = ""
        store.set_config(gid, "_profile", saved)
        log.info(tr("פרופיל הבוט בשרת %s עודכן"), guild["name"])
        return saved

    # ---- bot character (persona) per server -----------------------------------------------
    class PersonaIn(BaseModel):
        persona: dict
        apply_profile: bool = False
        avatar: str | None = None

    class PersonaGenIn(BaseModel):
        name: str
        universe: str = ""

    class PersonaTestIn(BaseModel):
        persona: dict
        messages: list[dict]

    def _chat_cfg(gid: str) -> dict:
        return catalog.merged_config("claude_chat", store.raw_config(gid, "claude_chat"), store.guild_lang(gid))

    @app.get("/api/guilds/{gid}/persona")
    async def get_persona(gid: str):
        from . import persona

        guild = _guild_or_404(gid)
        return {"persona": persona.clean(guild["config"].get("_persona")), "sliders": persona.sliders_json(),
                "categories": [[k, label] for k, label in persona.CATEGORIES]}

    @app.put("/api/guilds/{gid}/persona")
    async def put_persona(gid: str, body: PersonaIn):
        from . import persona

        guild = _guild_or_404(gid)
        try:
            p = persona.validate(body.persona)
        except ValueError as exc:
            raise err(400, str(exc))
        store.set_config(gid, "_persona", p)  # saved first: a refused profile change never loses the character
        chat = _cog(gid, "ClaudeChat")
        live = manager.live_guild(gid) if hasattr(manager, "live_guild") else None
        if chat is not None and live is not None:  # forget the recent chat, so the new personality starts fresh
            for channel_id in [c.id for c in live.channels] + [t.id for t in getattr(live, "threads", [])]:
                chat.history.pop(channel_id, None)
        log.info(tr("הדמות של הבוט בשרת %s נשמרה (%s)"), guild["name"], p["name"] or "-")
        warning = ""
        if body.apply_profile and p["name"]:  # the character's name (and picture) become the bot's profile in this server
            owner = manager.owner_of(gid) if hasattr(manager, "owner_of") else None
            try:
                if owner is None:
                    raise ValueError(tr("הבוט צריך להיות מחובר ובשרת הזה"))
                member = await discord_rest.update_member_profile(secrets_store.get(owner.token_name), gid,
                                                                  nick=p["name"][:32], avatar=body.avatar or None)
                uid = str((member.get("user") or {}).get("id") or "")
                url = discord_rest.member_asset_url(gid, uid, member, "avatar")
                if url:
                    p["avatar_url"] = url
                    store.set_config(gid, "_persona", p)
                profile = dict(guild["config"].get("_profile") or {})
                profile.update({"nick": member.get("nick") or "", **({"avatar_url": url} if url else {})})
                store.set_config(gid, "_profile", profile)
            except ValueError as exc:
                warning = str(exc)
            except discord_rest.DiscordAPIError as exc:
                warning = tr("הדמות נשמרה, אבל דיסקורד לא החליף את השם או התמונה של הבוט: {error}", error=exc.message)
            if warning:
                log.warning("%s", warning)
        return {"persona": p, "warning": warning}

    @app.post("/api/guilds/{gid}/persona/generate")
    async def generate_persona(gid: str, body: PersonaGenIn):
        from . import persona
        from .modules.claude_chat import ai_complete

        _guild_or_404(gid)
        name = body.name.strip()[:80]
        if not name:
            raise err(400, tr("לדמות צריך שם"))
        lang = i18n.LANGS[store.get_settings()["ui_lang"]]["name"]
        prompt = persona.GENERATE_PROMPT.format(name=name, universe=f" from {body.universe.strip()[:80]}" if body.universe.strip() else "",
                                                language=lang)
        try:
            text = await ai_complete(store, _chat_cfg(gid), "You write character profiles as JSON.",
                                     [{"role": "user", "content": prompt}], max_tokens=1200)
            return persona.parse_generated(text)
        except ValueError as exc:
            raise err(400, str(exc))

    @app.post("/api/guilds/{gid}/persona/test")
    async def test_persona(gid: str, body: PersonaTestIn):
        from . import persona
        from .modules.claude_chat import LANGUAGE_RULE, ai_complete

        _guild_or_404(gid)
        p = persona.clean({**body.persona, "enabled": True})
        if not p["name"]:
            raise err(400, tr("לדמות צריך שם"))
        msgs = [{"role": m.get("role"), "content": str(m.get("content") or "")[:2000]} for m in body.messages[-20:]
                if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()]
        if not msgs or msgs[-1]["role"] != "user":
            raise err(400, tr("כתוב הודעה"))
        try:
            reply = await ai_complete(store, _chat_cfg(gid), persona.build_prompt(p) + LANGUAGE_RULE, msgs)
        except ValueError as exc:
            raise err(400, str(exc))
        return {"reply": reply}

    @app.get("/api/guilds/{gid}/social/status")
    async def social_status(gid: str):
        _guild_or_404(gid)
        cfg = catalog.merged_config("social_alerts", store.raw_config(gid, "social_alerts"))
        out = []
        for i, feed in enumerate(cfg["feeds"]):
            st = store.get_kv(f"feed_status:{gid}:{feed['kind']}:{feed['source'].strip().lower()}") or {}
            out.append({"index": i, "kind": feed["kind"], "source": feed["source"], "channel_id": feed["channel_id"], **st})
        return out

    @app.post("/api/guilds/{gid}/social/test/{index}")
    async def social_test(gid: str, index: int, send: bool = False):
        guild = _live(gid)
        cfg = catalog.merged_config("social_alerts", store.raw_config(gid, "social_alerts"))
        if not 0 <= index < len(cfg["feeds"]):
            raise err(404, tr("לא נמצא"))
        cog = _cog(gid, "SocialAlerts")
        try:
            text = await cog.preview(guild, cfg["feeds"][index], send=send)
        except ValueError as exc:
            raise err(400, str(exc))
        except discord.HTTPException as exc:
            raise err(400, tr("דיסקורד דחה את ההודעה: {error}", error=exc.text or exc.status))
        except Exception as exc:  # noqa: BLE001 - network problems with the source site
            raise err(502, tr("המקור לא ענה: {error}", error=f"{type(exc).__name__}: {exc}"[:200]))
        return {"message": text}

    class BirthdayIn(BaseModel):
        name: str = ""
        user_id: str = ""
        date: str

    @app.get("/api/guilds/{gid}/birthdays")
    async def list_birthdays(gid: str):
        _guild_or_404(gid)
        return store.list_birthdays(gid)

    @app.post("/api/guilds/{gid}/birthdays")
    async def add_birthday(gid: str, body: BirthdayIn):
        from .modules.birthdays import parse_date

        _guild_or_404(gid)
        parsed = parse_date(body.date)
        if parsed is None:
            raise err(400, tr("תאריך לא תקין. כתוב יום/חודש, למשל 24/12 או 24/12/1995."))
        uid, name = body.user_id.strip(), body.name.strip()[:100]
        if uid and not valid_snowflake(uid):
            raise err(400, tr("מזהה משתמש לא תקין"))
        guild = manager.live_guild(gid)
        member = guild.get_member(int(uid)) if guild is not None and uid else None
        if member is not None:
            name = member.display_name
        if not uid and not name:
            raise err(400, tr("צריך שם או מזהה משתמש"))
        return {"id": store.set_birthday(gid, uid, name or uid, *parsed)}

    @app.delete("/api/guilds/{gid}/birthdays/{bid}")
    async def delete_birthday(gid: str, bid: int):
        _guild_or_404(gid)
        if not store.delete_birthday(gid, bid):
            raise err(404, tr("לא נמצא"))
        return {"ok": True}

    @app.get("/api/guilds/{gid}/giveaways")
    async def list_giveaways(gid: str):
        _guild_or_404(gid)
        return store.list_giveaways(gid)

    @app.get("/api/guilds/{gid}/invites")
    async def list_invites(gid: str):
        _guild_or_404(gid)
        return store.invite_board(gid, 50)

    @app.get("/api/guilds/{gid}/tickets")
    async def list_tickets(gid: str):
        _guild_or_404(gid)
        return store.list_tickets(gid)

    @app.get("/api/guilds/{gid}/suggestions")
    async def list_suggestions(gid: str):
        _guild_or_404(gid)
        return store.list_suggestions(gid)

    # ---- updates ------------------------------------------------------------------
    @app.get("/api/updates")
    async def get_updates():
        from . import updater

        repo = updater.repo_for(store)
        result = {"version": __version__, "repo": repo, "releases": [], "error": ""}
        if repo:
            try:
                result["releases"] = await updater.list_releases(repo)
            except ValueError as exc:
                result["error"] = str(exc)
        return result

    @app.put("/api/updates/repo")
    async def put_update_repo(body: RepoIn):
        from . import updater

        repo = body.repo.strip().removeprefix("https://github.com/").strip("/")
        if repo and not updater.REPO_RE.match(repo):
            raise err(400, tr("כתובת מאגר לא תקינה. כותבים בצורה owner/name"))
        store.set_kv("update_repo", repo)
        return await get_updates()

    @app.post("/api/updates/install")
    async def install_update(body: InstallIn):
        from . import updater

        repo = updater.repo_for(store)
        try:
            return await updater.install(repo, body.tag)
        except ValueError as exc:
            log.warning(tr("העדכון נכשל: %s"), exc)
            raise err(400, str(exc))
        except OSError as exc:  # e.g. a file locked by another program
            log.exception(tr("העדכון נכשל: %s"), exc)
            raise err(500, tr("העדכון נכשל: {error}", error=str(exc)[:200]))

    # ---- Claude chat ------------------------------------------------------
    @app.get("/api/claude/usage")
    async def claude_usage():
        from .modules.claude_chat import usage_summary

        return usage_summary(store)

    # ---- live events -----------------------------------------------------
    @app.websocket("/ws")
    async def events(ws: WebSocket):
        phone = from_phone(ws.scope)
        if phone and not phone_client_ok(ws.client.host if ws.client else None):
            await ws.close(code=1008)
            return
        if not phone and ws.headers.get("host") not in allowed_hosts:
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=5)
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
            await ws.close(code=1008)
            return
        if phone:
            device = phones.device_for(first.get("token") if isinstance(first, dict) else None)
            if device is None or not device.get("verified"):
                await ws.close(code=1008)
                return
        elif not isinstance(first, dict) or not token_ok(first.get("token")):
            await ws.close(code=1008)
            return
        queue = bus.subscribe()
        try:
            await ws.send_json({"type": "backlog", "logs": list(bus.backlog)})
            await ws.send_json({"type": "status", "status": manager.status()})
            while True:
                await ws.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(queue)

    if UI_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

    return app
