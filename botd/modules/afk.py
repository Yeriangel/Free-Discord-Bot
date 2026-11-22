"""AFK: /afk marks you away; people who mention you get an automatic reply until you write again."""

from ..i18n import N_, tr
import discord
from discord import app_commands
from discord.ext import commands

from .common import relative_time, reply_off

PREFIX = "[AFK] "


def can_rename(member: discord.Member) -> bool:
    me = member.guild.me
    return (me is not None and me.guild_permissions.manage_nicknames and member != member.guild.owner
            and member.top_role < me.top_role)


class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="afk", description=N_("סימון שאתה לא נמצא, עם סיבה"))
    @app_commands.guild_only()
    async def afk(self, interaction: discord.Interaction, reason: app_commands.Range[str, 0, 200] = ""):
        cfg = self.bot.cfg(interaction.guild, "afk")
        if not cfg:
            return await reply_off(interaction, self.bot)
        member, lang = interaction.user, cfg["_lang"]
        old_nick = None
        if cfg["nickname_prefix"] and can_rename(member) and not member.display_name.startswith(PREFIX):
            old_nick = member.nick or ""
            try:
                await member.edit(nick=(PREFIX + member.display_name)[:32], reason="AFK")
            except discord.HTTPException:
                old_nick = None
        self.bot.store.set_afk(str(interaction.guild_id), str(member.id), reason or tr("AFK", lang), old_nick)
        await interaction.response.send_message(tr("💤 סימנתי שאתה לא נמצא. כשתכתוב הודעה, הסימון יוסר.", lang), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        cfg = self.bot.cfg(message.guild, "afk")
        if not cfg:
            return
        gid, lang = str(message.guild.id), cfg["_lang"]
        back = self.bot.store.clear_afk(gid, str(message.author.id))
        if back is not None:
            if back["old_nick"] is not None and can_rename(message.author):
                try:
                    await message.author.edit(nick=back["old_nick"] or None, reason="back from AFK")
                except discord.HTTPException:
                    pass
            if cfg["welcome_back"]:
                try:
                    await message.reply(tr("👋 ברוך שובך {user}! הסרתי את סטטוס ה-AFK.", lang, user=message.author.display_name),
                                        mention_author=False, delete_after=10)
                except discord.HTTPException:
                    pass
        replies = []
        for user in message.mentions[:5]:
            if user.bot or user.id == message.author.id:
                continue
            row = self.bot.store.get_afk(gid, str(user.id))
            if row:
                replies.append(cfg["reply_message"].replace("{username}", discord.utils.escape_markdown(user.display_name))
                               .replace("{user}", discord.utils.escape_markdown(user.display_name))
                               .replace("{reason}", discord.utils.escape_markdown(row["reason"]))
                               .replace("{since}", relative_time(row["since"])))
        if replies:
            try:
                await message.reply("\n".join(replies)[:2000], mention_author=False,
                                    allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass
