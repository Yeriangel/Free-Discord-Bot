"""BotManager: starts/stops the discord.py client inside the service process."""

from .i18n import tr
import asyncio
import hashlib
import json
import logging
import time

import discord
from discord.ext import commands

from . import catalog, discord_rest, secrets_store
from .bus import bus
from .store import Store

log = logging.getLogger("botd.bot")

NO_PINGS = discord.AllowedMentions.none()
USER_PINGS = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False)


def _plain(text) -> str:
    """discord.py may wrap texts in locale_str; we want the plain source string."""
    return str(getattr(text, "message", text) or "")


class ManagedBot(commands.Bot):
    def __init__(self, manager: "BotManager", available: dict):
        intents = discord.Intents.default()
        intents.members = available.get("members", False)
        intents.message_content = available.get("message_content", False)
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=NO_PINGS,
        )
        self.manager = manager
        self.store: Store = manager.store

    # ---- helpers used by the module cogs ---------------------------------
    def cfg(self, guild: discord.abc.Snowflake | None, key: str) -> dict | None:
        """The module's config for this guild, or None if the module is off there."""
        if guild is None:
            return None
        gid = str(guild.id)
        if not self.manager.owns(gid):  # another of your bots runs this server
            return None
        if not self.store.module_enabled(gid, key):
            return None
        lang = self.store.guild_lang_obj(gid)
        config = catalog.merged_config(key, self.store.raw_config(gid, key), lang)
        config["_lang"] = lang
        return config

    def lang(self, guild: discord.abc.Snowflake | None) -> str:
        """The bot's language in this server (Hebrew by default)."""
        return self.store.guild_lang_obj(str(guild.id)) if guild is not None else "he"

    def t(self, guild, text: str, **kw) -> str:
        """Translate a bot message into the server's language."""
        return tr(text, self.lang(guild), **kw)

    async def send_log(self, guild: discord.Guild, embed: discord.Embed, event: str | None = None) -> None:
        cfg = self.cfg(guild, "logs")
        if not cfg or not cfg["channel_id"] or (event and not cfg.get(event, True)):
            return
        channel = guild.get_channel(int(cfg["channel_id"]))
        if channel is None or not hasattr(channel, "send"):
            return
        try:
            await channel.send(embed=embed, allowed_mentions=NO_PINGS)
        except discord.HTTPException as exc:
            log.warning(tr("לא הצלחתי לכתוב לערוץ הלוגים בשרת %s: %s"), guild.name, exc.text or exc.status)

    # ---- lifecycle events -------------------------------------------------
    async def setup_hook(self) -> None:
        from .modules import COGS

        for cog_cls in COGS:
            await self.add_cog(cog_cls(self))
        self.apply_command_custom()
        await self._sync_commands()

    def apply_command_custom(self) -> None:
        """Renames, re-describes or removes slash commands as set on the app's Commands page (all servers)."""
        custom = self.store.get_kv("command_custom", {}) or {}
        for cmd in list(self.tree.get_commands()):
            cmd.extras.setdefault("src_name", cmd.name)
            opts = custom.get(cmd.extras["src_name"]) or {}
            if opts.get("description"):
                cmd.extras["custom_description"] = opts["description"]
            if opts.get("enabled") is False:
                self.tree.remove_command(cmd.name)
            elif opts.get("name") and opts["name"] != cmd.name:
                self.tree.remove_command(cmd.name)
                cmd.name = opts["name"]
                self.tree.add_command(cmd)

    def _localize_commands(self) -> None:
        """Slash command descriptions follow the app's UI language (Discord has no Hebrew/Arabic client locale)."""
        from .i18n import ui_lang

        lang = ui_lang()

        def walk(cmds):
            for c in cmds:
                yield c
                if isinstance(c, discord.app_commands.Group):
                    yield from walk(c.commands)

        for cmd in walk(self.tree.get_commands()):
            if isinstance(cmd, discord.app_commands.ContextMenu):  # right-click commands: a name only
                continue
            src = cmd.extras.setdefault("src_description", _plain(cmd.description))
            cmd.description = (cmd.extras.get("custom_description") or tr(src, lang))[:100]
            for param in getattr(cmd, "_params", {}).values():
                if not hasattr(param, "_src_description"):
                    param._src_description = _plain(param.description)
                param.description = tr(param._src_description, lang)[:100]
                srcs = cmd.extras.setdefault(f"src_choices:{param.name}", [_plain(c.name) for c in param.choices or []])
                for choice, src in zip(param.choices or [], srcs):
                    choice.name = tr(src, lang)[:100]

    async def _sync_commands(self) -> None:
        self._localize_commands()
        try:
            payload = [c.to_dict(self.tree) for c in self.tree.get_commands()]
        except TypeError:  # older discord.py signature
            payload = [c.to_dict() for c in self.tree.get_commands()]
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = f"commands_hash:{self.application_id}"
        if self.store.get_kv(key) == digest:
            return
        try:
            synced = await self.tree.sync()
            self.store.set_kv(key, digest)
            log.info(tr("נרשמו %d פקודות סלאש בדיסקורד"), len(synced))
        except discord.HTTPException as exc:
            log.warning(tr("רישום פקודות הסלאש נכשל: %s"), exc.text or exc.status)

    async def on_ready(self) -> None:
        await self.manager.apply_presence()
        self.manager._sync_guilds(self)
        self.manager._set_state("connected")
        log.info(tr("מחובר כ-%s, ב-%d שרתים"), self.user, len(self.guilds))

    async def on_resumed(self) -> None:
        self.manager._set_state("connected")
        log.info(tr("החיבור חודש"))

    async def on_disconnect(self) -> None:
        if self.manager.state == "connected":
            self.manager._set_state("reconnecting")
            log.warning(tr("החיבור לדיסקורד נותק, מנסה להתחבר מחדש…"))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info(tr("הבוט נוסף לשרת %s"), guild.name)
        self.manager._sync_guilds(self)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info(tr("הבוט הוסר מהשרת %s"), guild.name)
        self.manager._publish_status()

    async def on_guild_update(self, _before: discord.Guild, _after: discord.Guild) -> None:
        self.manager._sync_guilds(self)


_COMMAND_SPECS: list[dict] | None = None


async def command_specs(store: Store) -> list[dict]:
    """Every top-level slash command the bot has (name, Hebrew source description, subcommands),
    worked out once by loading the modules into a bot that never connects."""
    global _COMMAND_SPECS
    if _COMMAND_SPECS is None:
        from .modules import COGS

        bot = ManagedBot(BotManager(store), {"members": False, "message_content": False})
        for cog_cls in COGS:
            await bot.add_cog(cog_cls(bot))
        _COMMAND_SPECS = [{"name": c.name, "description": _plain(c.description),
                           "subcommands": [s.name for s in getattr(c, "commands", [])]}
                          for c in bot.tree.get_commands() if not isinstance(c, discord.app_commands.ContextMenu)]
        for name in list(bot.cogs):
            await bot.remove_cog(name)
    return _COMMAND_SPECS


class BotManager:
    def __init__(self, store: Store, account_id: str = "main", token_name: str = "bot_token", label: str = ""):
        self.store = store
        self.account_id, self.token_name, self.label = account_id, token_name, label
        self.fleet: "BotFleet | None" = None
        self.bot: ManagedBot | None = None
        self.task: asyncio.Task | None = None
        self.state = "stopped"  # stopped | connecting | connected | reconnecting | stopping | error
        self.error = ""
        self.started_at: float | None = None
        self.intents: dict | None = None
        self.app_id = ""
        self._lock = asyncio.Lock()

    # ---- status ----------------------------------------------------------
    def owns(self, gid: str) -> bool:
        """True if this account runs that server (always true when it is the only bot)."""
        if self.fleet is None or not self.fleet.extras:
            return True
        return self.fleet.owner_of(gid) is self

    def status(self) -> dict:
        bot = self.bot
        user = None
        if bot is not None and bot.user is not None:
            user = {
                "id": str(bot.user.id),
                "name": bot.user.name,
                "avatar": bot.user.display_avatar.with_size(128).url,
                "has_avatar": bot.user.avatar is not None,
            }
        latency = None
        if bot is not None and self.state == "connected" and bot.latency == bot.latency:  # not NaN
            latency = round(bot.latency * 1000)
        return {
            "state": self.state,
            "error": self.error,
            "user": user,
            "guild_count": len(bot.guilds) if bot is not None and self.state == "connected" else None,
            "latency_ms": latency,
            "started_at": self.started_at,
            "intents": self.intents,
            "app_id": self.app_id,
        }

    def _publish_status(self) -> None:
        status = self.fleet.status() if self.fleet is not None else self.status()
        bus.publish({"type": "status", "status": status})

    def _set_state(self, state: str, error: str = "") -> None:
        self.state = state
        self.error = error
        self._publish_status()

    def _sync_guilds(self, bot: commands.Bot) -> None:
        for guild in bot.guilds:
            self.store.upsert_guild(str(guild.id), guild.name, guild.icon.url if guild.icon else "")
        self._publish_status()

    def live_guild_ids(self) -> set[str]:
        if self.bot is None or self.state != "connected":
            return set()
        return {str(g.id) for g in self.bot.guilds}

    def live_guild(self, gid: str) -> discord.Guild | None:
        if self.bot is None or self.state != "connected" or not gid.isdigit():
            return None
        return self.bot.get_guild(int(gid))

    def guild_objects(self, gid: str) -> dict | None:
        """Channels and roles for setting pickers (only while connected)."""
        guild = self.live_guild(gid)
        if guild is None:
            return None
        me = guild.me
        perms = me.guild_permissions if me else discord.Permissions.none()
        return {
            "text_channels": [
                {"id": str(c.id), "name": c.name, "category": c.category.name if c.category else ""}
                for c in guild.text_channels
            ],
            "voice_channels": [
                {"id": str(c.id), "name": c.name, "category": c.category.name if c.category else ""}
                for c in guild.voice_channels
            ],
            "roles": [
                {"id": str(r.id), "name": r.name, "assignable": me is not None and r < me.top_role}
                for r in reversed(guild.roles)
                if not r.is_default() and not r.managed
            ],
            "bot_permissions": [k for k in catalog.PERM_BITS if getattr(perms, k, False)],
        }

    # ---- presence (status + activity) -------------------------------------
    @staticmethod
    def build_presence(pr: dict):
        status = {"online": discord.Status.online, "idle": discord.Status.idle,
                  "dnd": discord.Status.dnd, "invisible": discord.Status.invisible}[pr.get("status", "online")]
        text, kind = pr.get("text", ""), pr.get("activity", "none")
        activity = None
        if kind == "playing":
            activity = discord.Game(name=text)
        elif kind == "streaming":
            activity = discord.Streaming(name=text, url=pr.get("url", ""))
        elif kind in ("listening", "watching", "competing"):
            activity = discord.Activity(type=getattr(discord.ActivityType, kind), name=text)
        elif kind == "custom":
            activity = discord.CustomActivity(name=text)
        return status, activity

    async def apply_presence(self) -> None:
        if self.bot is None or self.bot.ws is None:
            return
        status, activity = self.build_presence(self.store.get_settings()["presence"])
        try:
            await self.bot.change_presence(status=status, activity=activity)
        except Exception as exc:  # noqa: BLE001
            log.warning(tr("עדכון הסטטוס נכשל: %s"), exc)

    # ---- lifecycle -------------------------------------------------------
    async def _detect_intents(self, token: str) -> dict:
        try:
            app = await discord_rest.get_application(token)
        except discord_rest.DiscordAPIError:
            return {"members": False, "message_content": False}
        self.app_id = str(app.get("id") or "")
        found = discord_rest.intents_from_flags(int(app.get("flags") or 0))
        missing = [catalog.INTENT_LABELS[k] for k, v in found.items() if not v]
        if missing:
            log.warning(tr("ב-Developer Portal כבויים: %s. מודולים שצריכים אותם לא יעבדו."), ", ".join(missing))
        return found

    async def start(self) -> None:
        async with self._lock:
            if self.task is not None and not self.task.done():
                return
            token = secrets_store.get(self.token_name)
            if not token:
                raise ValueError(tr("לא הוגדר Bot Token. הוסף אותו בעמוד 'חיבור והגדרות'."))
            self.started_at = time.time()
            self._set_state("connecting")
            log.info(tr("מפעיל את הבוט…"))
            self.intents = await self._detect_intents(token)
            self.bot = ManagedBot(self, self.intents)
            self.task = asyncio.create_task(self._run(self.bot, token), name="discord-bot")

    async def _run(self, bot: ManagedBot, token: str) -> None:
        error = ""
        try:
            await bot.start(token, reconnect=True)
        except discord.LoginFailure:
            error = tr("הטוקן לא תקין. עדכן אותו בעמוד 'חיבור והגדרות'.")
        except discord.PrivilegedIntentsRequired:
            error = tr("חסרים Privileged Intents. הפעל אותם ב-Developer Portal, בלשונית Bot.")
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 - report anything to the UI
            log.exception(tr("הבוט נעצר בגלל שגיאה"))
            error = tr("שגיאה: {error}", error=type(exc).__name__)
        finally:
            if not bot.is_closed():
                await bot.close()
            if error:
                log.error(error)
            else:
                log.info(tr("הבוט כבוי"))
            self.started_at = None
            self._set_state("error" if error else "stopped", error)

    async def enable_intents(self) -> dict:
        """Turns the privileged intents on in the Developer Portal (via the API) and reconnects the bot."""
        token = secrets_store.get(self.token_name)
        if not token:
            raise ValueError(tr("לא הוגדר Bot Token. הוסף אותו בעמוד 'חיבור והגדרות'."))
        found = await discord_rest.enable_intents(token)
        log.info(tr("Intents הופעלו: Server Members %s, Message Content %s"),
                 "✓" if found["members"] else "✕", "✓" if found["message_content"] else "✕")
        if self.task is not None and not self.task.done():
            await self.stop()
            await self.start()
        else:
            self.intents = found
            self._publish_status()
        return found

    async def stop(self) -> None:
        async with self._lock:
            task, bot = self.task, self.bot
            if task is None or task.done():
                self._set_state("stopped")
                return
            self._set_state("stopping")
            log.info(tr("עוצר את הבוט…"))
            if bot is not None:
                await bot.close()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            except asyncio.TimeoutError:
                task.cancel()


# ---------------------------------------------------------------------------------------------------
# Several bot accounts at once. The main account uses "bot_token"; extra accounts have their own token.
# Each server is run by one account: the one chosen for it in the app, or else the main bot when it is
# there, so two of your bots in the same server never answer twice.
# ---------------------------------------------------------------------------------------------------
ACCOUNTS_KEY = "bot_accounts"


class BotFleet:
    def __init__(self, store: Store):
        self.store = store
        self.main = BotManager(store)
        self.main.fleet = self
        self.extras: dict[str, BotManager] = {}
        for acc in store.get_kv(ACCOUNTS_KEY, []) or []:
            self._add_manager(acc["id"], acc.get("label", ""))

    # ---- accounts ----------------------------------------------------------------------------
    def _add_manager(self, account_id: str, label: str) -> BotManager:
        m = BotManager(self.store, account_id, f"bot_token_{account_id}", label)
        m.fleet = self
        self.extras[account_id] = m
        return m

    def managers(self) -> list[BotManager]:
        return [self.main, *self.extras.values()]

    def get(self, account_id: str) -> BotManager | None:
        return self.main if account_id in ("", "main") else self.extras.get(account_id)

    def add_account(self, label: str) -> BotManager:
        import secrets as _secrets

        account_id = _secrets.token_hex(3)
        accounts = self.store.get_kv(ACCOUNTS_KEY, []) or []
        accounts.append({"id": account_id, "label": label[:40]})
        self.store.set_kv(ACCOUNTS_KEY, accounts)
        return self._add_manager(account_id, label[:40])

    async def remove_account(self, account_id: str) -> None:
        m = self.extras.pop(account_id, None)
        if m is not None:
            await m.stop()
            secrets_store.delete(m.token_name)
        self.store.set_kv(ACCOUNTS_KEY, [a for a in self.store.get_kv(ACCOUNTS_KEY, []) or [] if a["id"] != account_id])

    def accounts_status(self) -> list[dict]:
        out = []
        for m in self.managers():
            st = m.status()
            out.append({"id": m.account_id, "label": m.label or ("Main bot" if m.account_id == "main" else m.account_id),
                        "main": m.account_id == "main", "has_token": bool(secrets_store.get(m.token_name)),
                        "state": st["state"], "user": st["user"], "guild_count": st["guild_count"], "error": st["error"]})
        return out

    # ---- which account runs a server ------------------------------------------------------------
    def owner_of(self, gid: str) -> BotManager | None:
        present = [m for m in self.managers()
                   if m.bot is not None and m.state == "connected" and m.bot.get_guild(int(gid)) is not None]
        chosen = self.get(self.store.guild_bot(gid))
        if chosen is not None and chosen in present:
            return chosen
        if self.main in present:
            return self.main
        return present[0] if present else None

    def bot_for(self, gid: str):
        owner = self.owner_of(gid)
        return owner.bot if owner is not None else None

    # ---- the BotManager interface the API uses (main bot + all accounts together) -----------------
    def __getattr__(self, name):  # bot, state, task, error, intents, app_id, ... come from the main bot
        return getattr(self.main, name)

    def status(self) -> dict:
        st = self.main.status()
        st["accounts"] = self.accounts_status() if self.extras else []
        return st

    async def start(self) -> None:
        await self.main.start()
        for m in self.extras.values():
            if secrets_store.get(m.token_name):
                try:
                    await m.start()
                except ValueError as exc:
                    log.warning("%s: %s", m.label or m.account_id, exc)

    async def stop(self) -> None:
        for m in self.managers():
            await m.stop()

    async def enable_intents(self) -> dict:
        found = await self.main.enable_intents()
        for m in self.extras.values():
            if secrets_store.get(m.token_name):
                try:
                    await m.enable_intents()
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: %s", m.label or m.account_id, exc)
        return found

    async def apply_presence(self) -> None:
        for m in self.managers():
            await m.apply_presence()

    def live_guild_ids(self) -> set[str]:
        out: set[str] = set()
        for m in self.managers():
            out |= m.live_guild_ids()
        return out

    def live_guild(self, gid: str) -> discord.Guild | None:
        owner = self.owner_of(gid) if gid.isdigit() else None
        return owner.live_guild(gid) if owner is not None else None

    def guild_objects(self, gid: str) -> dict | None:
        owner = self.owner_of(gid) if gid.isdigit() else None
        if owner is None:
            return None
        objects = owner.guild_objects(gid)
        if objects is not None:
            objects["bot_account"] = owner.account_id
            objects["bots_present"] = [m.account_id for m in self.managers()
                                       if m.bot is not None and m.state == "connected" and m.bot.get_guild(int(gid))]
        return objects

    def all_bots(self) -> list:
        return [m.bot for m in self.managers() if m.bot is not None and m.state == "connected"]
