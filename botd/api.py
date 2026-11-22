"""Local control API for the UI. Listens on 127.0.0.1 only and requires a per-launch token."""

from .i18n import tr
import asyncio
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


class ProfileIn(BaseModel):
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
    emoji: str
    role_id: str


class ReactionRoleIn(BaseModel):
    channel_id: str
    content: str = ""
    pairs: list[RRPair]


class ModuleIn(BaseModel):
    enabled: bool


class InviteIn(BaseModel):
    permissions: list[str]
    guild_id: str = ""


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
    token_bytes = session_token.encode()

    def token_ok(value: str | None) -> bool:
        return hmac.compare_digest((value or "").encode("utf-8", "replace"), token_bytes)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        bus.attach(asyncio.get_running_loop())
        log.info(tr("שירות הבוט עלה (גרסה %s)"), __version__)
        if store.get_settings()["autostart_bot"] and secrets_store.get("bot_token"):
            try:
                await manager.start()
            except ValueError as exc:
                log.warning("%s", exc)
        yield
        await manager.stop()
        log.info(tr("שירות הבוט נסגר"))

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
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
            "img-src 'self' data: blob: https://cdn.discordapp.com; "
            f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    # ---- service ---------------------------------------------------------
    @app.get("/api/ping")
    async def ping():
        return {"ok": True, "version": __version__}

    @app.post("/api/shutdown")
    async def shutdown():
        log.info(tr("התקבלה בקשת כיבוי לשירות"))
        asyncio.get_running_loop().call_later(0.3, request_shutdown)
        return {"ok": True}

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

    # ---- settings & secrets ---------------------------------------------
    @app.get("/api/settings")
    async def get_settings():
        return {
            "secrets": {name: secrets_store.describe(name) for name in secrets_store.NAMES},
            "settings": store.get_settings(),
        }

    @app.put("/api/settings")
    async def put_settings(body: SettingsIn):
        try:
            store.update_settings(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise err(400, str(exc))
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
        raise err(404, tr("סוד לא מוכר"))

    # ---- bot profile (name / avatar) ------------------------------------
    @app.get("/api/profile")
    async def get_profile():
        try:
            me = await discord_rest.get_me(_require_token())
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)
        return _profile_view(me)

    def _profile_view(me: dict) -> dict:
        return {"id": me["id"], "username": me.get("username", ""), "discriminator": me.get("discriminator", "0"),
                "avatar_url": discord_rest.avatar_url(me), "banner_url": discord_rest.banner_url(me),
                "presence": store.get_settings()["presence"]}

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
        try:
            me = await discord_rest.update_me(_require_token(), body.username, body.avatar, body.banner)
        except ValueError as exc:
            raise err(400, str(exc))
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)
        log.info(tr("פרופיל הבוט עודכן (%s)"), ", ".join(k for k in ("username", "avatar", "banner") if getattr(body, k)))
        return _profile_view(me)

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
            app_info = await discord_rest.get_application(_require_token())
            url = catalog.invite_url(str(app_info["id"]), body.permissions, body.guild_id)
        except ValueError as exc:
            raise err(400, str(exc))
        except discord_rest.DiscordAPIError as exc:
            raise _discord_error(exc)
        return {"url": url, "permissions_value": str(catalog.permissions_value(body.permissions))}

    # ---- guilds ----------------------------------------------------------
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
        store.set_config(gid, key, clean)
        log.info(tr("ההגדרות של %s עודכנו בשרת %s"), key, guild["name"])
        return _guild_view(gid)

    def _rr_cog():
        cog = manager.bot.get_cog("ReactionRoles") if manager.bot is not None else None
        if cog is None or manager.state != "connected":
            raise err(409, tr("הבוט צריך להיות מחובר כדי לפרסם הודעה בדיסקורד"))
        return cog

    @app.post("/api/guilds/{gid}/reaction-roles")
    async def add_reaction_roles(gid: str, body: ReactionRoleIn):
        _guild_or_404(gid)
        cog = _rr_cog()
        guild = manager.live_guild(gid)
        if guild is None:
            raise err(409, tr("הבוט לא נמצא בשרת הזה"))
        try:
            await cog.publish(guild, body.channel_id, body.content, [p.model_dump() for p in body.pairs])
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
            cog = _rr_cog()
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
        return _guild_view(gid)

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
            raise err(400, str(exc))

    # ---- Claude chat ------------------------------------------------------
    @app.get("/api/claude/usage")
    async def claude_usage():
        from .modules.claude_chat import usage_summary

        return usage_summary(store)

    # ---- live events -----------------------------------------------------
    @app.websocket("/ws")
    async def events(ws: WebSocket):
        if ws.headers.get("host") not in allowed_hosts:
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=5)
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
            await ws.close(code=1008)
            return
        if not isinstance(first, dict) or not token_ok(first.get("token")):
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
