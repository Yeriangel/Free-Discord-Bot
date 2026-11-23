"""Restricted channels: per channel, pick what is NOT allowed (plain messages, links, images, videos, commands).
A message that contains something blocked is deleted, and the writer is told why."""

from ..i18n import N_, tr
import logging
import re

import discord
from discord.ext import commands

from ..bot import USER_PINGS

log = logging.getLogger("botd.restricted")

KINDS = ("text", "links", "images", "videos", "commands")
KIND_NAMES = {"text": N_("הודעות טקסט"), "links": N_("קישורים"), "images": N_("תמונות"), "videos": N_("סרטונים"),
              "commands": N_("פקודות")}
# the old "what is allowed" modes, as block lists
OLD_MODES = {"images": ["text", "links", "videos", "commands"], "videos": ["text", "links", "images", "commands"],
             "media": ["text", "links", "commands"], "commands": ["text", "links", "images", "videos"]}
URL_RE = re.compile(r"https?://\S+|discord\.gg/\S+|www\.\S+\.\S+", re.I)


def blocked_of(entry: dict) -> list[str]:
    if isinstance(entry.get("blocked"), list):
        return [k for k in entry["blocked"] if k in KINDS]
    return OLD_MODES.get(entry.get("mode", ""), [])


def message_kinds(content: str, attachment_types: list[str], embed_types: list[str], prefixes: list[str],
                  filenames: list[str] | None = None) -> set[str]:
    """What a message contains. "text" means a plain message: words only, nothing else."""
    names = [str(n or "").lower() for n in filenames or []]
    kinds = set()
    if any(t.startswith("image/") for t in attachment_types) or any(n.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")) for n in names):
        kinds.add("images")
    if any(t.startswith("video/") for t in attachment_types) or any(n.endswith((".mp4", ".mov", ".webm", ".mkv")) for n in names):
        kinds.add("videos")
    text = (content or "").strip()
    if URL_RE.search(text):
        kinds.add("links")
    elif any(t in ("image", "gifv") for t in embed_types):
        kinds.add("images")
    elif "video" in embed_types:
        kinds.add("videos")
    if text and any(p and text.startswith(p) for p in prefixes):
        kinds.add("commands")
    if text and not kinds:
        kinds.add("text")
    return kinds


def message_allowed(mode: str, content: str, attachment_types: list[str], embed_types: list[str],
                    prefixes: list[str]) -> bool:
    """Old-style check by mode (kept for the tests and old settings)."""
    kinds = message_kinds(content, attachment_types, embed_types, prefixes)
    return bool(kinds) and not (kinds & set(OLD_MODES.get(mode, [])))


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
        is_mod = isinstance(member, discord.Member) and (member.guild_permissions.manage_messages
                                                         or member.guild_permissions.administrator)
        if is_mod and cfg.get("exempt_mods"):
            return
        blocked = set(blocked_of(entry))
        kinds = message_kinds(message.content, [a.content_type or "" for a in message.attachments],
                              [e.type or "" for e in message.embeds], cfg["command_prefixes"],
                              [a.filename for a in message.attachments])
        hit = [k for k in KINDS if k in kinds and k in blocked]
        if not hit:
            return
        lang = self.bot.lang(message.guild)
        if not message.channel.permissions_for(message.guild.me).manage_messages:
            log.warning(tr("ערוץ מוגבל: לבוט אין הרשאת 'ניהול הודעות' ב-#%s בשרת %s"), message.channel.name, message.guild.name)
            return
        try:
            await message.delete()
        except discord.NotFound:
            return
        except discord.HTTPException as exc:
            log.warning(tr("ערוץ מוגבל: מחיקה נכשלה בשרת %s: %s"), message.guild.name, exc.text or exc.status)
            return
        text = tr("בערוץ הזה אסור לשלוח: {kinds}", lang, kinds=", ".join(tr(KIND_NAMES[k], lang) for k in hit))
        if cfg["reply_in_channel"]:
            try:
                await message.channel.send(f"{member.mention}, {text}.", delete_after=8, allowed_mentions=USER_PINGS)
            except discord.HTTPException:
                pass
        if cfg["notify_dm"]:
            try:
                await member.send(tr("ההודעה שלך ב-#{channel} בשרת **{server}** נמחקה: {reason}.", lang,
                                     channel=message.channel.name, server=discord.utils.escape_markdown(message.guild.name),
                                     reason=text))
            except discord.HTTPException:
                pass
