"""Reminders: repeating announcements set in the app, and personal /remind reminders."""

from ..i18n import N_, tr
import logging
import time
import zlib

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .common import parse_duration, relative_time, reply_off, text_channel

log = logging.getLogger("botd.reminders")
MAX_DELAY = 366 * 86400


def repeat_due(last: float, every_minutes: int, now: float) -> bool:
    return now - last >= every_minutes * 60


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.loop.start()

    async def cog_unload(self) -> None:
        self.loop.cancel()

    @tasks.loop(seconds=30)
    async def loop(self) -> None:
        now = time.time()
        await self._personal(now)
        await self._repeating(now)

    @loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    async def _personal(self, now: float) -> None:
        for r in self.bot.store.due_reminders(now):
            self.bot.store.delete_reminder(r["id"])
            guild = self.bot.get_guild(int(r["guild_id"])) if r["guild_id"] else None
            lang = self.bot.lang(guild)
            text = tr("⏰ תזכורת ל-<@{uid}>: {message}", lang, uid=r["user_id"], message=r["text"])
            channel = self.bot.get_channel(int(r["channel_id"])) if r["channel_id"] else None
            try:
                if channel is not None and hasattr(channel, "send"):
                    await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions(users=True))
                else:
                    user = self.bot.get_user(int(r["user_id"])) or await self.bot.fetch_user(int(r["user_id"]))
                    await user.send(text[:2000])
            except discord.HTTPException as exc:
                log.warning(tr("שליחת תזכורת נכשלה: %s"), exc.text or exc.status)

    async def _repeating(self, now: float) -> None:
        for guild in self.bot.guilds:
            cfg = self.bot.cfg(guild, "reminders")
            if not cfg:
                continue
            for i, item in enumerate(cfg["items"]):
                key = f"repeat:{guild.id}:{i}:{item['channel_id']}:{zlib.crc32(item['text'].encode()) & 0xFFFF}"
                last = self.bot.store.get_kv(key, 0)
                if not last:  # first run: start counting from now
                    self.bot.store.set_kv(key, now)
                    continue
                if not repeat_due(last, item["every_minutes"], now):
                    continue
                self.bot.store.set_kv(key, now)
                channel = text_channel(guild, item["channel_id"])
                if channel is None:
                    continue
                try:
                    await channel.send(item["text"][:2000], allowed_mentions=discord.AllowedMentions(users=True, roles=False))
                except discord.HTTPException as exc:
                    log.warning(tr("הודעה חוזרת נכשלה בשרת %s: %s"), guild.name, exc.text or exc.status)

    def add(self, guild: discord.Guild | None, channel_id: str, user_id: str, text: str, due: float) -> int:
        return self.bot.store.add_reminder(str(guild.id) if guild else "", channel_id, user_id, text, due)

    @app_commands.command(name="remind", description=N_("תזכורת אישית (למשל: 10m, 2h, 1d)"))
    @app_commands.describe(when=N_("בעוד כמה זמן: 10m, 2h, 1d, 1w"), text=N_("על מה להזכיר"))
    async def remind(self, interaction: discord.Interaction, when: str, text: app_commands.Range[str, 1, 1000]):
        lang = self.bot.lang(interaction.guild)
        if interaction.guild is not None:
            cfg = self.bot.cfg(interaction.guild, "reminders")
            if not cfg or not cfg["allow_personal"]:
                return await reply_off(interaction, self.bot)
            if len(self.bot.store.list_reminders(str(interaction.guild_id), str(interaction.user.id))) >= cfg["max_per_user"]:
                return await interaction.response.send_message(tr("הגעת למקסימום התזכורות הפתוחות.", lang), ephemeral=True)
        seconds = parse_duration(when)
        if not seconds or seconds > MAX_DELAY:
            return await interaction.response.send_message(tr("לא הבנתי את הזמן. כתוב למשל 10m, 2h, 1d או 1w.", lang), ephemeral=True)
        due = time.time() + seconds
        self.add(interaction.guild, str(interaction.channel_id or ""), str(interaction.user.id), text, due)
        await interaction.response.send_message(tr("⏰ אזכיר לך {when}.", lang, when=relative_time(due)), ephemeral=True)

    @app_commands.command(name="reminders", description=N_("התזכורות הפתוחות שלך"))
    async def reminders(self, interaction: discord.Interaction):
        lang = self.bot.lang(interaction.guild)
        rows = self.bot.store.list_reminders(uid=str(interaction.user.id))
        if not rows:
            return await interaction.response.send_message(tr("אין לך תזכורות פתוחות.", lang), ephemeral=True)
        lines = [f"`#{r['id']}` {relative_time(r['due'])} · {discord.utils.escape_markdown(r['text'][:80])}" for r in rows[:20]]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="reminder-delete", description=N_("מחיקת תזכורת לפי המספר שלה"))
    async def reminder_delete(self, interaction: discord.Interaction, number: int):
        lang = self.bot.lang(interaction.guild)
        ok = self.bot.store.delete_reminder(number, uid=str(interaction.user.id))
        await interaction.response.send_message(tr("🗑️ התזכורת נמחקה.", lang) if ok else tr("התזכורת לא נמצאה.", lang),
                                                ephemeral=True)
