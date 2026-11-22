"""Birthdays: members save their birthday; on the day the bot posts a greeting and can give a role."""

from ..i18n import N_, tr
import calendar
import datetime as dt
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import USER_PINGS
from .common import can_manage_role, reply_off, text_channel

log = logging.getLogger("botd.birthdays")


def parse_date(text: str) -> tuple[int, int, int] | None:
    """'24/12', '24.12.1995', '1995-12-24' -> (month, day, year or 0). Day comes first, as in most of the world."""
    text = (text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?", text)
        if not m:
            return None
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3) or 0)
        if 0 < year < 100:
            year += 1900 if year > dt.date.today().year % 100 else 2000
    try:
        dt.date(year or 2000, month, day)  # 2000 is a leap year, so 29/2 is allowed
    except ValueError:
        return None
    if year and not 1900 <= year <= dt.date.today().year:
        return None
    return month, day, year


def is_birthday_today(month: int, day: int, today: dt.date) -> bool:
    if (month, day) == (today.month, today.day):
        return True
    # 29 February birthdays are celebrated on 28 February in other years
    return (month, day) == (2, 29) and (today.month, today.day) == (2, 28) and not calendar.isleap(today.year)


def fmt_date(row: dict) -> str:
    return f"{row['day']:02d}/{row['month']:02d}" + (f"/{row['year']}" if row.get("year") else "")


class Birthdays(commands.Cog):
    birthday = app_commands.Group(name="birthday", description=N_("ימי הולדת"), guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        self.loop.start()

    async def cog_unload(self) -> None:
        self.loop.cancel()

    def save(self, guild: discord.Guild, member: discord.Member | None, name: str, month: int, day: int, year: int) -> int:
        uid = str(member.id) if member else ""
        return self.bot.store.set_birthday(str(guild.id), uid, member.display_name if member else name, month, day, year)

    @tasks.loop(minutes=5)
    async def loop(self) -> None:
        now = dt.datetime.now()
        for guild in self.bot.guilds:
            cfg = self.bot.cfg(guild, "birthdays")
            if not cfg:
                continue
            try:
                await self._remove_old_roles(guild, cfg, now.date())
                if now.hour >= cfg["hour"]:
                    await self._announce(guild, cfg, now.date())
            except discord.HTTPException as exc:
                log.warning(tr("ברכת יום הולדת נכשלה בשרת %s: %s"), guild.name, exc.text or exc.status)

    @loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    async def _announce(self, guild: discord.Guild, cfg: dict, today: dt.date) -> None:
        rows = [r for r in self.bot.store.list_birthdays(str(guild.id))
                if is_birthday_today(r["month"], r["day"], today) and r["last_announced"] != today.year]
        if not rows:
            return
        channel = text_channel(guild, cfg["channel_id"])
        role = guild.get_role(int(cfg["role_id"])) if cfg["role_id"] else None
        given = self.bot.store.get_kv(f"bday_roles:{guild.id}", [])
        for r in rows:
            self.bot.store.mark_birthday_announced(r["id"], today.year)
            member = guild.get_member(int(r["user_id"])) if r["user_id"] else None
            age = str(today.year - r["year"]) if r["year"] else ""
            text = (cfg["message"].replace("{user}", member.mention if member else f"**{discord.utils.escape_markdown(r['user_name'])}**")
                    .replace("{username}", discord.utils.escape_markdown(member.display_name if member else r["user_name"]))
                    .replace("{age}", age))
            if channel is not None:
                await channel.send(text[:2000], allowed_mentions=USER_PINGS)
            if member is not None and role is not None and can_manage_role(guild, role):
                await member.add_roles(role, reason="birthday")
                given.append([str(member.id), today.isoformat()])
        self.bot.store.set_kv(f"bday_roles:{guild.id}", given)

    async def _remove_old_roles(self, guild: discord.Guild, cfg: dict, today: dt.date) -> None:
        given = self.bot.store.get_kv(f"bday_roles:{guild.id}", [])
        keep = [g for g in given if g[1] == today.isoformat()]
        if len(keep) == len(given):
            return
        role = guild.get_role(int(cfg["role_id"])) if cfg["role_id"] else None
        for uid, day in given:
            if day != today.isoformat() and role is not None:
                member = guild.get_member(int(uid))
                if member is not None and role in member.roles and can_manage_role(guild, role):
                    await member.remove_roles(role, reason="birthday over")
        self.bot.store.set_kv(f"bday_roles:{guild.id}", keep)

    @birthday.command(name="set", description=N_("רישום יום ההולדת שלך (למשל 24/12 או 24/12/1995)"))
    async def set_cmd(self, interaction: discord.Interaction, date: str):
        cfg = self.bot.cfg(interaction.guild, "birthdays")
        if not cfg:
            return await reply_off(interaction, self.bot)
        lang = cfg["_lang"]
        if not cfg["allow_self"] and not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(tr("רישום ימי הולדת עצמי כבוי בשרת הזה.", lang), ephemeral=True)
        parsed = parse_date(date)
        if parsed is None:
            return await interaction.response.send_message(tr("תאריך לא תקין. כתוב יום/חודש, למשל 24/12 או 24/12/1995.", lang),
                                                           ephemeral=True)
        self.save(interaction.guild, interaction.user, "", *parsed)
        await interaction.response.send_message(tr("🎂 שמרתי: {date}", lang, date=fmt_date({"month": parsed[0], "day": parsed[1],
                                                                                         "year": parsed[2]})), ephemeral=True)

    @birthday.command(name="add", description=N_("רישום יום הולדת של חבר אחר (למנהלים)"))
    async def add_cmd(self, interaction: discord.Interaction, member: discord.Member, date: str):
        cfg = self.bot.cfg(interaction.guild, "birthdays")
        if not cfg:
            return await reply_off(interaction, self.bot)
        lang = cfg["_lang"]
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(tr("רק מנהלי שרת יכולים לרשום ימי הולדת של אחרים.", lang), ephemeral=True)
        parsed = parse_date(date)
        if parsed is None:
            return await interaction.response.send_message(tr("תאריך לא תקין. כתוב יום/חודש, למשל 24/12 או 24/12/1995.", lang),
                                                           ephemeral=True)
        self.save(interaction.guild, member, "", *parsed)
        await interaction.response.send_message(tr("🎂 שמרתי את יום ההולדת של {user}: {date}", lang, user=member.mention,
                                                   date=fmt_date({"month": parsed[0], "day": parsed[1], "year": parsed[2]})),
                                                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @birthday.command(name="remove", description=N_("מחיקת יום ההולדת שלך"))
    async def remove_cmd(self, interaction: discord.Interaction):
        cfg = self.bot.cfg(interaction.guild, "birthdays")
        if not cfg:
            return await reply_off(interaction, self.bot)
        row = self.bot.store.get_birthday(str(interaction.guild_id), str(interaction.user.id))
        if row:
            self.bot.store.delete_birthday(str(interaction.guild_id), row["id"])
        await interaction.response.send_message(tr("🗑️ נמחק.", cfg["_lang"]) if row else tr("לא רשום לך יום הולדת.", cfg["_lang"]),
                                                ephemeral=True)

    @birthday.command(name="list", description=N_("ימי ההולדת הקרובים"))
    async def list_cmd(self, interaction: discord.Interaction):
        cfg = self.bot.cfg(interaction.guild, "birthdays")
        if not cfg:
            return await reply_off(interaction, self.bot)
        rows = self.bot.store.list_birthdays(str(interaction.guild_id))
        if not rows:
            return await interaction.response.send_message(tr("עוד לא נרשמו ימי הולדת.", cfg["_lang"]), ephemeral=True)
        today = dt.date.today()
        rows.sort(key=lambda r: ((r["month"], r["day"]) < (today.month, today.day), r["month"], r["day"]))
        lines = [f"🎂 `{r['day']:02d}/{r['month']:02d}` · " + (f"<@{r['user_id']}>" if r["user_id"] else
                                                              discord.utils.escape_markdown(r["user_name"])) for r in rows[:20]]
        embed = discord.Embed(title=tr("ימי ההולדת הקרובים", cfg["_lang"]), description="\n".join(lines), color=0xF47FFF)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())
