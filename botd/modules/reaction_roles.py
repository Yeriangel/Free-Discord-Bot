"""Reaction roles: the bot posts a message with reactions; reacting gives/removes a role."""

from ..i18n import tr
import logging

import discord
from discord.ext import commands

from .. import catalog
from .common import can_manage_role, text_channel

log = logging.getLogger("botd.reaction_roles")

MAX_PAIRS = 20


def emoji_key(emoji) -> str:
    pe = emoji if isinstance(emoji, discord.PartialEmoji) else discord.PartialEmoji.from_str(str(emoji).strip())
    return str(pe.id) if pe.id else (pe.name or "")


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _groups(self, gid: str) -> list[dict]:
        return catalog.merged_config("reaction_roles", self.bot.store.raw_config(gid, "reaction_roles"))["groups"]

    def _save(self, gid: str, groups: list[dict]) -> None:
        self.bot.store.set_config(gid, "reaction_roles", {"groups": groups})

    def _role_for(self, payload: discord.RawReactionActionEvent) -> tuple[discord.Guild, discord.Role] | None:
        if payload.guild_id is None or (self.bot.user and payload.user_id == self.bot.user.id):
            return None
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not self.bot.cfg(guild, "reaction_roles"):
            return None
        key = emoji_key(payload.emoji)
        for group in self._groups(str(guild.id)):
            if group.get("message_id") != str(payload.message_id):
                continue
            for pair in group.get("pairs", []):
                if emoji_key(pair["emoji"]) == key:
                    role = guild.get_role(int(pair["role_id"]))
                    if can_manage_role(guild, role):
                        return guild, role
                    log.warning(tr("תפקיד לפי ריאקשן: אין לבוט אפשרות לתת את התפקיד בשרת %s"), guild.name)
        return None

    async def _member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        return None if member.bot else member

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        found = self._role_for(payload)
        if not found:
            return
        guild, role = found
        member = payload.member if payload.member and not payload.member.bot else await self._member(guild, payload.user_id)
        if member is not None and role not in member.roles:
            try:
                await member.add_roles(role, reason=tr("תפקיד לפי ריאקשן", self.bot.lang(guild)))
            except discord.HTTPException as exc:
                log.warning(tr("הוספת תפקיד נכשלה: %s"), exc.text or exc.status)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        found = self._role_for(payload)
        if not found:
            return
        guild, role = found
        member = await self._member(guild, payload.user_id)
        if member is not None and role in member.roles:
            try:
                await member.remove_roles(role, reason=tr("תפקיד לפי ריאקשן", self.bot.lang(guild)))
            except discord.HTTPException as exc:
                log.warning(tr("הסרת תפקיד נכשלה: %s"), exc.text or exc.status)

    # ---- called from the local API -----------------------------------------
    async def publish(self, guild: discord.Guild, channel_id: str, content: str, pairs: list[dict]) -> dict:
        channel = text_channel(guild, channel_id)
        if channel is None:
            raise ValueError(tr("בחר ערוץ טקסט"))
        perms = channel.permissions_for(guild.me)
        if not (perms.send_messages and perms.add_reactions and perms.read_message_history):
            raise ValueError(tr("לבוט אין בערוץ הזה הרשאות שליחה, הוספת ריאקשנים וקריאת היסטוריה"))
        if not 1 <= len(pairs) <= MAX_PAIRS:
            raise ValueError(tr("צריך בין 1 ל-{n} זוגות של אימוג'י ותפקיד", n=MAX_PAIRS))
        clean, seen = [], set()
        for pair in pairs:
            emoji = str(pair.get("emoji", "")).strip()
            role = guild.get_role(int(pair["role_id"])) if str(pair.get("role_id", "")).isdigit() else None
            if not emoji or role is None:
                raise ValueError(tr("לכל שורה צריך אימוג'י ותפקיד"))
            if not can_manage_role(guild, role):
                raise ValueError(tr("התפקיד @{role} מעל תפקיד הבוט, או שלבוט אין 'ניהול תפקידים'", role=role.name))
            key = emoji_key(emoji)
            if key in seen:
                raise ValueError(tr("האימוג'י {emoji} מופיע פעמיים", emoji=emoji))
            seen.add(key)
            clean.append({"emoji": emoji, "role_id": str(role.id), "role_name": role.name})
        text = content.strip() or tr("**בחרו תפקיד:**", self.bot.lang(guild)) + "\n" + "\n".join(f"{p['emoji']}  <@&{p['role_id']}>" for p in clean)
        if len(text) > 2000:
            raise ValueError(tr("ההודעה ארוכה מדי"))
        message = await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        try:
            for pair in clean:
                await message.add_reaction(pair["emoji"])
        except discord.HTTPException:
            await message.delete()
            raise ValueError(tr("אחד האימוג'ים לא תקין, או שהבוט לא יכול להשתמש בו")) from None
        group = {"channel_id": str(channel.id), "channel_name": channel.name, "message_id": str(message.id),
                 "content": text, "pairs": clean}
        groups = self._groups(str(guild.id)) + [group]
        self._save(str(guild.id), groups)
        log.info(tr("פורסמה הודעת תפקידים ב-#%s בשרת %s"), channel.name, guild.name)
        return group

    async def remove(self, guild_id: str, message_id: str, delete_message: bool) -> None:
        groups = self._groups(guild_id)
        group = next((g for g in groups if g.get("message_id") == message_id), None)
        if group is None:
            raise KeyError(message_id)
        self._save(guild_id, [g for g in groups if g is not group])
        if delete_message:
            guild = self.bot.get_guild(int(guild_id))
            channel = text_channel(guild, group["channel_id"]) if guild else None
            if channel is not None:
                try:
                    await channel.get_partial_message(int(message_id)).delete()
                except discord.HTTPException as exc:
                    log.warning(tr("מחיקת הודעת התפקידים נכשלה: %s"), exc.text or exc.status)
        log.info(tr("הודעת תפקידים %s הוסרה"), message_id)
