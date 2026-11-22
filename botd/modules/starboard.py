"""Starboard: messages that get enough of one reaction are copied to a showcase channel.
Also the always-on message counter that lets Claude answer "who talks the most here"."""

from ..i18n import tr
import asyncio
import logging
from collections import defaultdict

import discord
from discord.ext import commands

from .common import text_channel, themed
from .reaction_roles import emoji_key

log = logging.getLogger("botd.starboard")


class Activity(commands.Cog):
    """Counts messages per member (no content is stored). Runs in every server the bot is in."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        self.bot.store.count_message(str(message.guild.id), str(message.author.id), message.author.display_name)


def star_embed(message: discord.Message, count: int, emoji: str, lang: str) -> discord.Embed:
    embed = discord.Embed(description=(message.content or "")[:4000] or None, color=themed(lang, 0xF5C542), timestamp=message.created_at)
    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
    image = next((a.url for a in message.attachments if (a.content_type or "").startswith("image/")), None)
    if image:
        embed.set_image(url=image)
    embed.add_field(name=tr("מקור", lang), value=f"[{tr('מעבר להודעה', lang)}]({message.jump_url})")
    embed.set_footer(text=f"{emoji} {count}")
    return embed


class Starboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._update(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._update(payload)

    async def _update(self, payload: discord.RawReactionActionEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        cfg = self.bot.cfg(guild, "starboard")
        if not cfg or not cfg["channel_id"] or emoji_key(str(payload.emoji)) != emoji_key(cfg["emoji"]):
            return
        if str(payload.channel_id) == cfg["channel_id"] or str(payload.channel_id) in cfg["ignored_channel_ids"]:
            return
        board = text_channel(guild, cfg["channel_id"])
        source = guild.get_channel_or_thread(payload.channel_id)
        if board is None or source is None or getattr(source, "nsfw", False):
            return
        async with self._locks[payload.message_id]:
            try:
                message = await source.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            reaction = next((r for r in message.reactions if emoji_key(str(r.emoji)) == emoji_key(cfg["emoji"])), None)
            count = reaction.count if reaction else 0
            if reaction and not cfg["self_star"] and count:
                async for user in reaction.users(limit=100):
                    if user.id == message.author.id:
                        count -= 1
                        break
            gid, lang = str(guild.id), cfg.get("_lang", "he")
            existing = self.bot.store.star_entry(gid, str(message.id))
            try:
                if count >= cfg["threshold"]:
                    content = f"{cfg['emoji']} **{count}** · {source.mention}"
                    embed = star_embed(message, count, cfg["emoji"], lang)
                    if existing:
                        try:
                            star = await board.fetch_message(int(existing))
                            await star.edit(content=content, embed=embed)
                            return
                        except discord.NotFound:
                            pass
                    star = await board.send(content, embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    self.bot.store.set_star_entry(gid, str(message.id), str(star.id))
                elif existing:
                    self.bot.store.set_star_entry(gid, str(message.id), None)
                    try:
                        await (await board.fetch_message(int(existing))).delete()
                    except discord.HTTPException:
                        pass
            except discord.HTTPException as exc:
                log.warning(tr("Starboard נכשל בשרת %s: %s"), guild.name, exc.text or exc.status)
