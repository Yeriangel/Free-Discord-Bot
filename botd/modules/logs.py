"""Server event log to a chosen channel."""

import discord

from ..i18n import N_, tr
from discord.ext import commands

from .common import COLOR_DANGER, COLOR_INFO, COLOR_OK, COLOR_WARN, themed, user_line


NO_TEXT = N_("*(ללא טקסט)*")
NOT_CACHED = N_("*ההודעה לא הייתה בזיכרון של הבוט*")
_FIXED = {NO_TEXT, NOT_CACHED}


def _clip(text: str, limit: int = 1000) -> str:
    text = text or NO_TEXT
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ServerLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _is_log_channel(self, guild: discord.Guild, channel_id: int) -> bool:
        cfg = self.bot.cfg(guild, "logs")
        return bool(cfg and cfg["channel_id"] == str(channel_id))

    async def _send(self, guild, event, title, color, fields=(), description=None):
        lang = self.bot.lang(guild)
        embed = discord.Embed(title=tr(title, lang), color=themed(lang, color), description=description, timestamp=discord.utils.utcnow())
        for name, value in fields:
            embed.add_field(name=tr(name, lang), value=tr(value, lang) if value in _FIXED else value, inline=False)
        await self.bot.send_log(guild, embed, event)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None or self._is_log_channel(guild, payload.channel_id):
            return
        msg = payload.cached_message
        if msg is not None and msg.author.bot:
            return
        fields = [(N_("ערוץ"), f"<#{payload.channel_id}>")]
        if msg is not None:
            fields += [(N_("כותב"), user_line(msg.author)), (N_("תוכן"), _clip(msg.content))]
        else:
            fields.append((N_("תוכן"), NOT_CACHED))
        await self._send(guild, "message_delete", N_("🗑 הודעה נמחקה"), COLOR_DANGER, fields)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        if self._is_log_channel(before.guild, before.channel.id):
            return
        await self._send(before.guild, "message_edit", N_("✏ הודעה נערכה"), COLOR_WARN, [
            (N_("ערוץ"), f"{before.channel.mention} · [{tr('להודעה', self.bot.lang(before.guild))}]({after.jump_url})"),
            (N_("כותב"), user_line(before.author)),
            (N_("לפני"), _clip(before.content)),
            (N_("אחרי"), _clip(after.content)),
        ])

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._send(member.guild, "member_join", N_("📥 חבר הצטרף"), COLOR_OK, [
            (N_("משתמש"), user_line(member)),
            (N_("החשבון נוצר"), discord.utils.format_dt(member.created_at, "R")),
        ])

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self._send(member.guild, "member_leave", N_("📤 חבר עזב"), COLOR_INFO, [(N_("משתמש"), user_line(member))])

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await self._send(guild, "member_ban", N_("⛔ משתמש נחסם"), COLOR_DANGER, [(N_("משתמש"), user_line(user))])

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        await self._send(guild, "member_ban", N_("✅ החסימה בוטלה"), COLOR_OK, [(N_("משתמש"), user_line(user))])

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        added = [r.mention for r in after.roles if r not in before.roles]
        removed = [r.mention for r in before.roles if r not in after.roles]
        if added or removed:
            fields = [(N_("משתמש"), user_line(after))]
            if added:
                fields.append((N_("נוספו"), " ".join(added)))
            if removed:
                fields.append((N_("הוסרו"), " ".join(removed)))
            await self._send(after.guild, "member_roles", N_("🎭 תפקידים השתנו"), COLOR_INFO, fields)
        if before.nick != after.nick:
            await self._send(after.guild, "member_nick", N_("🏷 כינוי השתנה"), COLOR_INFO, [
                (N_("משתמש"), user_line(after)),
                (N_("לפני"), discord.utils.escape_markdown(before.nick or "—")),
                (N_("אחרי"), discord.utils.escape_markdown(after.nick or "—")),
            ])

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self._send(channel.guild, "channels", N_("➕ ערוץ נוצר"), COLOR_OK, [(N_("ערוץ"), f"{channel.mention} (`{channel.name}`)")])

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self._send(channel.guild, "channels", N_("➖ ערוץ נמחק"), COLOR_DANGER, [(N_("ערוץ"), f"`{channel.name}`")])
