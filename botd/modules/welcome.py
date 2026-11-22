"""Welcome and leave messages."""

from ..i18n import tr
import logging

import discord
from discord.ext import commands

from ..bot import USER_PINGS
from .common import fill, text_channel

log = logging.getLogger("botd.welcome")


def parse_color(value: str, default: int = 0x6D5DFC) -> int:
    value = (value or "").strip().lstrip("#")
    try:
        return int(value, 16) if len(value) == 6 else default
    except ValueError:
        return default


def welcome_embed(cfg: dict, text: str, member: discord.Member) -> discord.Embed:
    embed = discord.Embed(title=fill(cfg["embed_title"], member=member, guild=member.guild, lang=cfg.get("_lang", "he"))[:256] or None,
                          description=text, color=parse_color(cfg["embed_color"]))
    embed.set_thumbnail(url=member.display_avatar.url)
    return embed


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = self.bot.cfg(member.guild, "welcome")
        if not cfg:
            return
        text = fill(cfg["welcome_message"], member=member, guild=member.guild, lang=self.bot.lang(member.guild))
        if not text.strip():
            return
        channel = text_channel(member.guild, cfg["channel_id"])
        if channel is not None:
            try:
                if cfg["use_embed"]:
                    await channel.send(embed=welcome_embed(cfg, text, member), allowed_mentions=USER_PINGS)
                else:
                    await channel.send(text, allowed_mentions=USER_PINGS)
            except discord.HTTPException as exc:
                log.warning(tr("הודעת ברכה נכשלה בשרת %s: %s"), member.guild.name, exc.text or exc.status)
        if cfg["dm_welcome"] and not member.bot:
            try:
                await member.send(text, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass  # user has DMs closed

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        cfg = self.bot.cfg(member.guild, "welcome")
        if not cfg or not cfg["leave_enabled"]:
            return
        text = fill(cfg["leave_message"], member=member, guild=member.guild, lang=self.bot.lang(member.guild))
        channel = text_channel(member.guild, cfg["leave_channel_id"] or cfg["channel_id"])
        if channel is None or not text.strip():
            return
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            log.warning(tr("הודעת עזיבה נכשלה בשרת %s: %s"), member.guild.name, exc.text or exc.status)
