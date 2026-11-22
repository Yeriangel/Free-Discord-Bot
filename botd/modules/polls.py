"""Polls using Discord's own poll feature (live results, one or more answers)."""

from ..i18n import N_, tr
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from .common import has_any_role, reply_off


def split_answers(text: str) -> list[str]:
    """'Pizza | Burger | Sushi' or 'Pizza, Burger' -> answers (2 to 10, each up to 55 characters)."""
    sep = "|" if "|" in text else ","
    answers = [a.strip()[:55] for a in text.split(sep) if a.strip()]
    return list(dict.fromkeys(answers))[:10]


def build_poll(question: str, answers: list[str], hours: int, multiple: bool = False) -> discord.Poll:
    poll = discord.Poll(question=question[:300], duration=dt.timedelta(hours=max(1, min(hours, 768))), multiple=multiple)
    for answer in answers:
        poll.add_answer(text=answer)
    return poll


class Polls(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def can_create(self, member: discord.Member, cfg: dict) -> bool:
        return cfg["allow_everyone"] or member.guild_permissions.manage_messages or has_any_role(member, cfg["creator_role_ids"])

    @app_commands.command(name="poll", description=N_("יצירת סקר"))
    @app_commands.guild_only()
    @app_commands.describe(question=N_("השאלה"), answers=N_("התשובות, מופרדות ב-| (למשל: פיצה | המבורגר | סושי)"),
                           hours=N_("כמה שעות הסקר פתוח"), multiple=N_("לאפשר לבחור כמה תשובות"))
    async def poll(self, interaction: discord.Interaction, question: app_commands.Range[str, 1, 300], answers: str,
                   hours: app_commands.Range[int, 1, 768] = 24, multiple: bool = False):
        cfg = self.bot.cfg(interaction.guild, "polls")
        if not cfg:
            return await reply_off(interaction, self.bot)
        lang = cfg["_lang"]
        if not self.can_create(interaction.user, cfg):
            return await interaction.response.send_message(tr("אין לך הרשאה ליצור סקרים בשרת הזה.", lang), ephemeral=True)
        items = split_answers(answers)
        if len(items) < 2:
            return await interaction.response.send_message(tr("צריך לפחות 2 תשובות, מופרדות ב-|", lang), ephemeral=True)
        await interaction.response.send_message(poll=build_poll(question, items, min(hours, cfg["max_hours"]), multiple))
