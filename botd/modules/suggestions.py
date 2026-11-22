"""Suggestion box: /suggest (or any message in the suggestions channel) becomes a numbered card with votes."""

from ..i18n import N_, tr
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .common import has_any_role, reply_off, text_channel

log = logging.getLogger("botd.suggestions")

STATUS = {  # key: (label, color)
    "open": (N_("ממתינה להצבעה"), 0x6D5DFC),
    "approved": (N_("✅ אושרה"), 0x2FBF71),
    "denied": (N_("❌ נדחתה"), 0xEF4B5F),
    "considering": (N_("🤔 בבדיקה"), 0xF0B232),
    "done": (N_("🚀 בוצעה"), 0x3BA55C),
}


def suggestion_embed(number: int, text: str, author: discord.abc.User | None, status: str, note: str,
                     lang: str) -> discord.Embed:
    label, color = STATUS.get(status, STATUS["open"])
    embed = discord.Embed(title=tr("💡 הצעה #{n}", lang, n=number), description=text[:4000], color=color)
    if author is not None:
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
    embed.add_field(name=tr("סטטוס", lang), value=tr(label, lang), inline=True)
    if note:
        embed.add_field(name=tr("הערה מהצוות", lang), value=note[:1000], inline=False)
    return embed


class Suggestions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def post(self, guild: discord.Guild, member: discord.Member, text: str) -> discord.Message | None:
        cfg = self.bot.cfg(guild, "suggestions")
        channel = text_channel(guild, cfg["channel_id"]) if cfg else None
        if channel is None:
            return None
        lang = cfg["_lang"]
        sid, number = self.bot.store.add_suggestion(str(guild.id), str(channel.id), str(member.id), str(member), text)
        embed = suggestion_embed(number, text, None if cfg["anonymous"] else member, "open", "", lang)
        msg = await channel.send(embed=embed)
        self.bot.store.set_suggestion_message(sid, str(msg.id))
        for emoji in ("👍", "👎"):
            try:
                await msg.add_reaction(emoji)
            except discord.HTTPException:
                break
        if cfg["create_thread"]:
            try:
                await msg.create_thread(name=f"#{number} · {text[:80]}", auto_archive_duration=10080)
            except discord.HTTPException:
                pass
        return msg

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not message.content.strip():
            return
        cfg = self.bot.cfg(message.guild, "suggestions")
        if not cfg or not cfg["convert_messages"] or str(message.channel.id) != cfg["channel_id"]:
            return
        try:
            await message.delete()
        except discord.HTTPException:
            return  # no permission: leave the message as it is
        try:
            await self.post(message.guild, message.author, message.content)
        except discord.HTTPException as exc:
            log.warning(tr("פרסום הצעה נכשל בשרת %s: %s"), message.guild.name, exc.text or exc.status)

    @app_commands.command(name="suggest", description=N_("שליחת הצעה לשרת"))
    @app_commands.guild_only()
    async def suggest(self, interaction: discord.Interaction, text: app_commands.Range[str, 3, 1500]):
        cfg = self.bot.cfg(interaction.guild, "suggestions")
        if not cfg:
            return await reply_off(interaction, self.bot)
        try:
            msg = await self.post(interaction.guild, interaction.user, text)
        except discord.HTTPException as exc:
            return await interaction.response.send_message(str(exc.text or exc.status), ephemeral=True)
        if msg is None:
            return await interaction.response.send_message(tr("לא הוגדר ערוץ הצעות באפליקציה.", cfg["_lang"]), ephemeral=True)
        await interaction.response.send_message(tr("✅ ההצעה נשלחה: {link}", cfg["_lang"], link=msg.jump_url), ephemeral=True)

    @app_commands.command(name="suggestion", description=N_("שינוי הסטטוס של הצעה (לצוות)"))
    @app_commands.guild_only()
    @app_commands.choices(status=[app_commands.Choice(name=label, value=key) for key, (label, _c) in STATUS.items()])
    async def suggestion(self, interaction: discord.Interaction, number: int, status: app_commands.Choice[str],
                         note: str = ""):
        cfg = self.bot.cfg(interaction.guild, "suggestions")
        if not cfg:
            return await reply_off(interaction, self.bot)
        lang = cfg["_lang"]
        member = interaction.user
        if not (member.guild_permissions.manage_messages or has_any_role(member, cfg["reviewer_role_ids"])):
            return await interaction.response.send_message(tr("אין לך הרשאה לשנות סטטוס של הצעות.", lang), ephemeral=True)
        row = self.bot.store.get_suggestion(str(interaction.guild_id), number)
        if row is None:
            return await interaction.response.send_message(tr("הצעה #{n} לא נמצאה.", lang, n=number), ephemeral=True)
        self.bot.store.set_suggestion_status(str(interaction.guild_id), number, status.value, note)
        channel = interaction.guild.get_channel(int(row["channel_id"]))
        author = interaction.guild.get_member(int(row["user_id"])) if not cfg["anonymous"] else None
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            await msg.edit(embed=suggestion_embed(number, row["text"], author, status.value, note, lang))
        except (discord.HTTPException, AttributeError, ValueError):
            pass
        await interaction.response.send_message(tr("הצעה #{n}: {status}", lang, n=number, status=tr(STATUS[status.value][0], lang)),
                                                ephemeral=True)
