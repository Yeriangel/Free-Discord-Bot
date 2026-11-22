"""Automations engine: WHEN SOMEONE -> IF -> DO THIS, MEE6-style."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import discord
from discord.ext import commands

from ..bot import USER_PINGS
from ..i18n import tr
from .common import can_manage_role, fill
from .reaction_roles import emoji_key

log = logging.getLogger("botd.automations")

BUTTON_PREFIX = "auto:"
RETRY_DELAYS = (5, 30, 120)  # seconds, used when "retry on failure" is on


@dataclass
class Event:
    """Everything a trigger knows; conditions read it, actions use it."""
    type: str
    member: object = None
    channel: object = None
    message: object = None            # message the actions may act on
    content: str = ""                 # message text (also for deleted messages, if cached)
    attachments: list = field(default_factory=list)   # content types
    is_reply: bool = False
    emoji: str = ""
    role: object = None
    button: str = ""

    @property
    def channel_ids(self) -> set[str]:
        ch = self.channel
        if ch is None:
            return set()
        ids = {str(ch.id)}
        for attr in ("parent_id", "category_id"):
            v = getattr(ch, attr, None)
            if isinstance(v, int) and v:
                ids.add(str(v))
        parent = getattr(ch, "parent", None)
        cat = getattr(parent, "category_id", None) if parent is not None else None
        if isinstance(cat, int) and cat:
            ids.add(str(cat))
        return ids

    @property
    def role_ids(self) -> set[str]:
        return {str(r.id) for r in getattr(self.member, "roles", []) or []}


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def condition_passes(cond: dict, ev: Event) -> bool:
    """Pure check of one IF condition."""
    t, vals = cond["type"], cond.get("values", [])
    roles = ev.role_ids
    if t == "in_channels":
        return bool(ev.channel_ids & set(vals))
    if t == "has_any_role":
        return bool(roles & set(vals))
    if t == "has_all_roles":
        return set(vals) <= roles
    if t == "lacks_any_role":
        return not set(vals) <= roles
    if t == "lacks_all_roles":
        return not roles & set(vals)
    if t == "user_is":
        return str(getattr(ev.member, "id", "")) in vals
    text = _norm(ev.content)
    words = [_norm(v) for v in vals]
    if t == "content_contains":
        return any(w and w in text for w in words)
    if t == "content_exact":
        return text in words
    if t == "content_starts":
        return any(w and text.startswith(w) for w in words)
    if t == "content_not_contains":
        return not any(w and w in text for w in words)
    if t == "reacted_with":
        return emoji_key(ev.emoji) in {emoji_key(v) for v in vals}
    if t == "button_is":
        return _norm(ev.button) in words
    if t in ("gained_role", "lost_role"):
        return ev.role is not None and str(ev.role.id) in vals
    kinds = ev.attachments
    if t == "has_image":
        return any(k.startswith("image/") for k in kinds)
    if t == "has_video":
        return any(k.startswith("video/") for k in kinds)
    if t == "has_audio":
        return any(k.startswith("audio/") for k in kinds)
    if t == "has_text_file":
        return any(k.startswith("text/") for k in kinds)
    if t == "is_reply":
        return ev.is_reply
    if t == "is_not_reply":
        return not ev.is_reply
    return False


def automation_matches(auto: dict, ev: Event) -> bool:
    if not auto["enabled"] or auto["trigger"] != ev.type:
        return False
    if auto.get("ignore_bots", True) and getattr(ev.member, "bot", False):
        return False
    return all(condition_passes(c, ev) for c in auto["conditions"])


class AutomationButtons(discord.ui.View):
    def __init__(self, labels: list[str]):
        super().__init__(timeout=None)
        for label in labels:
            self.add_item(discord.ui.Button(label=label[:80], style=discord.ButtonStyle.secondary,
                                            custom_id=(BUTTON_PREFIX + label)[:100]))


class Automations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last: dict[tuple[str, int], float] = {}

    def _autos(self, guild) -> list[dict]:
        cfg = self.bot.cfg(guild, "automations")
        return cfg["items"] if cfg else []

    async def fire(self, guild: discord.Guild, ev: Event) -> int:
        if ev.member is None:
            return 0
        fired = 0
        for auto in self._autos(guild):
            if not automation_matches(auto, ev):
                continue
            key = (auto["id"], ev.member.id)
            now = time.monotonic()
            if auto["cooldown"] and now - self._last.get(key, -1e9) < auto["cooldown"]:
                continue
            self._last[key] = now
            fired += 1
            log.info(tr("אוטומציה '%s' הופעלה בשרת %s"), auto["name"], guild.name)
            message = ev.message
            for action in auto["actions"]:
                await self._run_with_retry(auto, action, guild, ev, message)
                if action["type"] == "delete_message":
                    message = None  # nothing left to act on
        return fired

    async def _run_with_retry(self, auto, action, guild, ev, message) -> None:
        delays = RETRY_DELAYS if auto.get("retry") else ()
        for attempt in range(len(delays) + 1):
            try:
                await self._run(action, guild, ev, message)
                return
            except discord.HTTPException as exc:
                if (exc.status == 429 or exc.status >= 500) and attempt < len(delays):
                    await asyncio.sleep(delays[attempt])
                    continue
                log.warning(tr("אוטומציה '%s', פעולה %s נכשלה: %s"), auto["name"], action["type"], exc.text or exc.status)
                return
            except Exception:  # noqa: BLE001 - one bad action must not stop the others
                log.exception(tr("אוטומציה '%s', פעולה %s נכשלה"), auto["name"], action["type"])
                return

    def _text(self, template: str, ev: Event) -> str:
        member = ev.member
        text = fill(template, member=member, guild=member.guild, channel=ev.channel, lang=self.bot.lang(member.guild))
        text = text.replace("{message}", ev.content[:500])
        return text.replace("{role}", ev.role.name if ev.role is not None else "")[:2000]

    async def _run(self, action: dict, guild, ev: Event, message) -> None:
        t, member = action["type"], ev.member
        lang = self.bot.lang(guild)
        if t == "send_message":
            text = self._text(action["text"], ev)
            kwargs = {"view": AutomationButtons(action["buttons"])} if action.get("buttons") else {}
            if action["target"] == "dm":
                await member.send(text, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
                return
            target = ev.channel if action["target"] == "same_channel" else guild.get_channel(int(action["channel_id"]))
            if target is not None:
                await target.send(text, allowed_mentions=USER_PINGS, **kwargs)
        elif t == "reply" and message is not None:
            await message.reply(self._text(action["text"], ev), allowed_mentions=USER_PINGS)
        elif t in ("add_roles", "remove_roles") and isinstance(member, discord.Member):
            roles = [r for rid in action["roles"] if (r := guild.get_role(int(rid))) is not None]
            usable = [r for r in roles if can_manage_role(guild, r)]
            if len(usable) < len(roles):
                log.warning(tr("אוטומציה: אין לבוט אפשרות לנהל את התפקיד %s"),
                            ", ".join(r.name for r in roles if r not in usable))
            if t == "add_roles":
                usable = [r for r in usable if r not in member.roles]
                if usable:
                    await member.add_roles(*usable, reason=tr("אוטומציה", lang))
            else:
                usable = [r for r in usable if r in member.roles]
                if usable:
                    await member.remove_roles(*usable, reason=tr("אוטומציה", lang))
        elif t in ("give_coins", "take_coins"):
            eco = self.bot.cfg(guild, "economy") or {}
            gid, uid = str(guild.id), str(member.id)
            delta = action["amount"] if t == "give_coins" else -action["amount"]
            if self.bot.store.add_coins(gid, uid, str(member), delta, eco.get("start_balance", 0)) is None:
                have = self.bot.store.wallet(gid, uid)["balance"]  # not enough: take what there is
                self.bot.store.add_coins(gid, uid, str(member), -have)
        elif t in ("give_xp", "take_xp"):
            delta = action["amount"] if t == "give_xp" else -action["amount"]
            old, new = self.bot.store.change_xp(str(guild.id), str(member.id), str(member), delta)
            levels, cfg = self.bot.get_cog("Levels"), self.bot.cfg(guild, "levels")
            if levels is not None and cfg and isinstance(member, discord.Member):
                await levels.apply_change(member, old, new, cfg, ev.channel)
        elif t == "add_reaction" and message is not None:
            await message.add_reaction(action["emoji"])
        elif t == "pin_message" and message is not None:
            await message.pin(reason=tr("אוטומציה", lang))
        elif t == "delete_message" and message is not None:
            await message.delete()
        elif t == "create_thread" and message is not None and hasattr(message, "create_thread"):
            await message.create_thread(name=self._text(action["name"], ev)[:90] or tr("דיון", lang))

    # ---- event sources -------------------------------------------------------------------
    @staticmethod
    def _message_event(kind: str, message: discord.Message, keep_message: bool = True) -> Event:
        return Event(kind, member=message.author, channel=message.channel,
                     message=message if keep_message else None, content=message.content or "",
                     attachments=[a.content_type or "" for a in message.attachments],
                     is_reply=message.reference is not None)

    def _mine(self, user_id) -> bool:
        return self.bot.user is not None and user_id == self.bot.user.id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or self._mine(message.author.id) or not self._autos(message.guild):
            return
        await self.fire(message.guild, self._message_event("message_send", message))

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or self._mine(after.author.id) or before.content == after.content:
            return
        if self._autos(after.guild):
            await self.fire(after.guild, self._message_event("message_edit", after))

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        msg = payload.cached_message
        if payload.guild_id is None or msg is None or self._mine(msg.author.id):
            return  # without the cached message we don't know whose message it was
        if self._autos(msg.guild):
            await self.fire(msg.guild, self._message_event("message_delete", msg, keep_message=False))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.member is None or self._mine(payload.user_id):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not self._autos(guild):
            return
        channel = guild.get_channel_or_thread(payload.channel_id)
        if channel is None:
            return
        await self.fire(guild, Event("reaction_add", member=payload.member, channel=channel,
                                     message=channel.get_partial_message(payload.message_id), emoji=str(payload.emoji)))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        data = interaction.data or {}
        cid = data.get("custom_id", "") if interaction.type == discord.InteractionType.component else ""
        if not cid.startswith(BUTTON_PREFIX) or interaction.guild is None:
            return
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
        await self.fire(interaction.guild, Event("button_click", member=interaction.user, channel=interaction.channel,
                                                 message=interaction.message, button=cid[len(BUTTON_PREFIX):]))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not self._autos(after.guild):
            return
        for r in after.roles:
            if r not in before.roles:
                await self.fire(after.guild, Event("role_gain", member=after, role=r))
        for r in before.roles:
            if r not in after.roles:
                await self.fire(after.guild, Event("role_lose", member=after, role=r))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel or not self._autos(member.guild):
            return
        if before.channel is not None:
            await self.fire(member.guild, Event("voice_leave", member=member, channel=before.channel))
        if after.channel is not None:
            await self.fire(member.guild, Event("voice_join", member=member, channel=after.channel))

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        owner = thread.owner or (thread.guild.get_member(thread.owner_id) if thread.owner_id else None)
        if owner is None or self._mine(owner.id) or not self._autos(thread.guild):
            return
        await self.fire(thread.guild, Event("thread_create", member=owner, channel=thread))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if self._autos(member.guild):
            await self.fire(member.guild, Event("member_join", member=member))

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # keep buttons of earlier automation messages clickable after a restart
        labels = sorted({b for g in self.bot.guilds for a in self._autos(g) for act in a["actions"]
                         for b in act.get("buttons", [])})
        if labels:
            self.bot.add_view(AutomationButtons(labels[:25]))
