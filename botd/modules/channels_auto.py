"""Channels the bot manages by itself: statistics counters and temporary voice channels."""

from ..i18n import tr
import logging

import discord
from discord.ext import commands, tasks

log = logging.getLogger("botd.channels")


def count_for(guild: discord.Guild, kind: str, members_cached: bool) -> str:
    if kind == "members":
        return f"{guild.member_count or 0:,}"
    if kind in ("humans", "bots"):
        if not members_cached:
            return "?"
        n = sum(1 for m in guild.members if m.bot == (kind == "bots"))
        return f"{n:,}"
    if kind == "channels":
        return f"{sum(1 for c in guild.channels if not isinstance(c, discord.CategoryChannel)):,}"
    if kind == "roles":
        return f"{max(0, len(guild.roles) - 1):,}"
    if kind == "boosts":
        return f"{guild.premium_subscription_count or 0:,}"
    return "?"


class StatsChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.loop.start()

    async def cog_unload(self) -> None:
        self.loop.cancel()

    @tasks.loop(minutes=10)
    async def loop(self) -> None:
        for guild in self.bot.guilds:
            cfg = self.bot.cfg(guild, "stats")
            if cfg:
                try:
                    await self.update(guild, cfg)
                except discord.HTTPException as exc:
                    log.warning(tr("עדכון ערוצי הסטטיסטיקה נכשל בשרת %s: %s"), guild.name, exc.text or exc.status)

    @loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    async def update(self, guild: discord.Guild, cfg: dict) -> None:
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels:
            return
        key = f"stats:{guild.id}"
        state = self.bot.store.get_kv(key, {"category": "", "channels": []})
        locked = {guild.default_role: discord.PermissionOverwrite(connect=False),
                  me: discord.PermissionOverwrite(connect=True, manage_channels=True, view_channel=True)}
        category = guild.get_channel(int(state["category"])) if state["category"] else None
        if not isinstance(category, discord.CategoryChannel):
            category = await guild.create_category(cfg["category_name"] or "📊", overwrites=locked, position=0,
                                                   reason="stats channels")
            state["category"] = str(category.id)
        elif category.name != cfg["category_name"] and cfg["category_name"]:
            await category.edit(name=cfg["category_name"])
        cached = self.bot.intents.members and guild.chunked
        channels = []
        for i, counter in enumerate(cfg["counters"]):
            name = counter["name"].replace("{count}", count_for(guild, counter["kind"], cached))[:100]
            old_id = state["channels"][i] if i < len(state["channels"]) else ""
            channel = guild.get_channel(int(old_id)) if old_id else None
            if channel is None:
                channel = await guild.create_voice_channel(name, category=category, overwrites=locked, reason="stats channels")
            elif channel.name != name:
                await channel.edit(name=name)
            channels.append(str(channel.id))
        for extra in state["channels"][len(cfg["counters"]):]:
            channel = guild.get_channel(int(extra)) if extra else None
            if channel is not None:
                await channel.delete(reason="stats counter removed")
        state["channels"] = channels
        self.bot.store.set_kv(key, state)


class TempChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _ids(self, guild: discord.Guild) -> list[str]:
        return self.bot.store.get_kv(f"temp:{guild.id}", [])

    def _save(self, guild: discord.Guild, ids: list[str]) -> None:
        self.bot.store.set_kv(f"temp:{guild.id}", ids[-200:])

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:  # remove channels that emptied while the bot was off
            ids = self._ids(guild)
            for cid in list(ids):
                channel = guild.get_channel(int(cid))
                if channel is None:
                    ids.remove(cid)
                elif not channel.members:
                    try:
                        await channel.delete(reason="temporary channel empty")
                        ids.remove(cid)
                    except discord.HTTPException:
                        pass
            self._save(guild, ids)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        guild = member.guild
        cfg = self.bot.cfg(guild, "temp_channels")
        if before.channel is not None and before.channel != after.channel:
            ids = self._ids(guild)
            if str(before.channel.id) in ids and not before.channel.members:
                try:
                    await before.channel.delete(reason="temporary channel empty")
                except discord.HTTPException:
                    pass
                ids.remove(str(before.channel.id))
                self._save(guild, ids)
        if not cfg or after.channel is None or str(after.channel.id) != cfg["lobby_channel_id"] or member.bot:
            return
        lobby = after.channel
        name = cfg["name_template"].replace("{username}", member.display_name).replace("{user}", member.display_name)[:100]
        overwrites = dict(lobby.overwrites)
        if cfg["owner_can_manage"]:
            overwrites[member] = discord.PermissionOverwrite(manage_channels=True, connect=True, move_members=True)
        create = dict(name=name or "🔊", category=lobby.category, user_limit=cfg["user_limit"] or None,
                      reason=f"temporary channel for {member}")
        try:
            try:
                channel = await guild.create_voice_channel(**create, overwrites=overwrites)
            except discord.Forbidden:  # the lobby has overwrites the bot may not copy: use the category's
                channel = await guild.create_voice_channel(**create)
            await member.move_to(channel)
        except discord.HTTPException as exc:
            log.warning(tr("יצירת ערוץ זמני נכשלה בשרת %s: %s"), guild.name, exc.text or exc.status)
            return
        ids = self._ids(guild)
        ids.append(str(channel.id))
        self._save(guild, ids)
