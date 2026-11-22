"""Restricted channels: images-only, videos-only, media-only or commands-only. Other messages are deleted."""

from ..i18n import N_, tr
import logging

import discord
from discord.ext import commands

from ..bot import USER_PINGS

log = logging.getLogger("botd.restricted")

MODE_TEXT = {
    "images": N_("בערוץ הזה מותר לשלוח רק תמונות"),
    "videos": N_("בערוץ הזה מותר לשלוח רק סרטונים"),
    "media": N_("בערוץ הזה מותר לשלוח רק תמונות או סרטונים"),
    "commands": N_("בערוץ הזה מותר לשלוח רק פקודות"),
}


def message_allowed(mode: str, content: str, attachment_types: list[str], embed_types: list[str],
                    prefixes: list[str]) -> bool:
    """Pure check: does a message fit the channel's mode?"""
    images = any(t.startswith("image/") for t in attachment_types) or any(t in ("image", "gifv") for t in embed_types)
    videos = any(t.startswith("video/") for t in attachment_types) or "video" in embed_types
    if mode == "images":
        return images
    if mode == "videos":
        return videos
    if mode == "media":
        return images or videos
    if mode == "commands":
        return any(p and content.lstrip().startswith(p) for p in prefixes)
    return True


class RestrictedChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        cfg = self.bot.cfg(message.guild, "restricted_channels")
        if not cfg or not cfg["channels"]:
            return
        ids = {str(message.channel.id), str(getattr(message.channel, "parent_id", "") or "")}
        entry = next((c for c in cfg["channels"] if c["channel_id"] in ids), None)
        if entry is None:
            return
        member = message.author
        if isinstance(member, discord.Member) and (member.guild_permissions.manage_messages or member.guild_permissions.administrator):
            return
        if message_allowed(entry["mode"], message.content,
                           [a.content_type or "" for a in message.attachments],
                           [e.type or "" for e in message.embeds], cfg["command_prefixes"]):
            return
        try:
            await message.delete()
        except discord.NotFound:
            return
        except discord.HTTPException as exc:
            log.warning(tr("ערוץ מוגבל: מחיקה נכשלה בשרת %s: %s"), message.guild.name, exc.text or exc.status)
            return
        lang = self.bot.lang(message.guild)
        text = tr(MODE_TEXT.get(entry["mode"], ""), lang)
        if cfg["reply_in_channel"]:
            try:
                await message.channel.send(f"{member.mention}, {text}.", delete_after=6, allowed_mentions=USER_PINGS)
            except discord.HTTPException:
                pass
        if cfg["notify_dm"]:
            try:
                await member.send(tr("ההודעה שלך ב-#{channel} בשרת **{server}** נמחקה: {reason}.", lang,
                                     channel=message.channel.name, server=discord.utils.escape_markdown(message.guild.name),
                                     reason=text))
            except discord.HTTPException:
                pass
