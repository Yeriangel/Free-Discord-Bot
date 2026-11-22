"""Levels: XP for chatting, level-up messages, role rewards, /rank, /levels, /give-xp, /remove-xp."""

from ..i18n import N_, tr
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import USER_PINGS
from .common import can_manage_role
from .welcome import parse_color

log = logging.getLogger("botd.levels")

XP_MIN, XP_MAX = 15, 25      # XP per counted message (before the rate multiplier)
COOLDOWN = 60                # seconds between counted messages per user


# ---- level math (the classic curve: 5·L² + 50·L + 100 XP to go from L to L+1) ----
def xp_to_next(level: int) -> int:
    return 5 * level * level + 50 * level + 100


def total_xp_for_level(level: int) -> int:
    return sum(xp_to_next(lv) for lv in range(level))


def level_info(xp: int) -> tuple[int, int, int]:
    """Returns (level, XP into the current level, XP needed for the next level)."""
    level, remaining = 0, max(0, xp)
    while remaining >= xp_to_next(level):
        remaining -= xp_to_next(level)
        level += 1
    return level, remaining, xp_to_next(level)


def progress_bar(current: int, needed: int, width: int = 16) -> str:
    filled = round(width * current / needed) if needed else width
    return "▰" * filled + "▱" * (width - filled)


def gets_xp(role_ids: set[str], channel_ids: set[str], cfg: dict) -> bool:
    """No-XP rules. 'deny' = listed items get no XP; 'allow' = only listed items get XP."""
    roles, chans = set(cfg["no_xp_role_ids"]), set(cfg["no_xp_channel_ids"])
    if cfg["roles_mode"] == "deny" and role_ids & roles:
        return False
    if cfg["roles_mode"] == "allow" and roles and not role_ids & roles:
        return False
    if cfg["channels_mode"] == "deny" and channel_ids & chans:
        return False
    if cfg["channels_mode"] == "allow" and chans and not channel_ids & chans:
        return False
    return True


def reward_plan(rewards: list[dict], level: int, mode: str, remove_on_loss: bool,
                current_role_ids: set[str]) -> tuple[set[str], set[str]]:
    """Which reward roles to add and remove for a member at `level`."""
    earned = sorted((r for r in rewards if r["level"] <= level), key=lambda r: r["level"])
    reward_ids = {r["role_id"] for r in rewards}
    if mode == "highest":
        target = {earned[-1]["role_id"]} if earned else set()
        remove = (reward_ids - target) & current_role_ids
        if not remove_on_loss:  # keep roles above the current level
            remove -= {r["role_id"] for r in rewards if r["level"] > level}
    else:
        target = {r["role_id"] for r in earned}
        remove = {r["role_id"] for r in rewards if r["level"] > level} & current_role_ids if remove_on_loss else set()
        remove -= target
    return target - current_role_ids, remove


def fill_levelup(template: str, member: discord.Member, level: int) -> str:  # noqa: D103
    return (template.replace("{player}", member.mention)
            .replace("{player_name}", discord.utils.escape_markdown(member.display_name))
            .replace("{level}", str(level))
            .replace("{server}", discord.utils.escape_markdown(member.guild.name)))[:2000]


class Levels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---- XP from messages ------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        cfg = self.bot.cfg(message.guild, "levels")
        if not cfg:
            return
        channel_ids = {str(message.channel.id), str(getattr(message.channel, "parent_id", "") or "")}
        if not gets_xp({str(r.id) for r in message.author.roles}, channel_ids, cfg):
            return
        amount = max(1, round(random.randint(XP_MIN, XP_MAX) * float(cfg["rate"])))
        result = self.bot.store.gain_xp(str(message.guild.id), str(message.author.id), str(message.author),
                                        amount, time.time(), COOLDOWN)
        if result:
            await self.apply_change(message.author, *result, cfg, message.channel)

    async def apply_change(self, member: discord.Member, old_xp: int, new_xp: int, cfg: dict,
                           channel: discord.abc.Messageable | None = None) -> None:
        old_level, new_level = level_info(old_xp)[0], level_info(new_xp)[0]
        if new_level != old_level:
            await self.sync_roles(member, new_level, cfg)
        if new_level > old_level:
            log.info(tr("%s עלה לרמה %d בשרת %s"), member, new_level, member.guild.name)
            await self.announce(member, new_level, cfg, channel)

    async def sync_roles(self, member: discord.Member, level: int, cfg: dict) -> None:
        if not cfg["rewards"]:
            return
        add_ids, remove_ids = reward_plan(cfg["rewards"], level, cfg["reward_mode"], cfg["remove_on_loss"],
                                          {str(r.id) for r in member.roles})
        guild = member.guild
        to_add = [r for i in add_ids if can_manage_role(guild, r := guild.get_role(int(i)))]
        to_remove = [r for i in remove_ids if can_manage_role(guild, r := guild.get_role(int(i)))]
        try:
            if to_add:
                await member.add_roles(*to_add, reason=tr("פרס רמה {n}", cfg.get("_lang", "he"), n=level))
            if to_remove:
                await member.remove_roles(*to_remove, reason=tr("עדכון פרסי רמות (רמה {n})", cfg.get("_lang", "he"), n=level))
        except discord.HTTPException as exc:
            log.warning(tr("עדכון תפקידי רמות נכשל: %s"), exc.text or exc.status)
        if len(to_add) < len(add_ids) or len(to_remove) < len(remove_ids):
            log.warning(tr("חלק מתפקידי הרמות מעל תפקיד הבוט או לא קיימים בשרת %s"), guild.name)

    async def announce(self, member: discord.Member, level: int, cfg: dict, channel) -> None:
        mode = cfg["announce_mode"]
        if mode == "off":
            return
        text = fill_levelup(cfg["announce_message"], member, level)
        try:
            if mode == "dm":
                await member.send(text, allowed_mentions=discord.AllowedMentions.none())
                return
            target = channel
            if mode == "channel":
                target = member.guild.get_channel(int(cfg["announce_channel_id"])) if cfg["announce_channel_id"] else None
            if target is not None:
                await target.send(text, allowed_mentions=USER_PINGS)
        except discord.HTTPException as exc:
            log.debug(tr("הודעת עליית רמה לא נשלחה: %s"), exc.text or exc.status)

    # ---- slash commands ------------------------------------------------------------
    def _rank_embed(self, member: discord.Member, cfg: dict) -> discord.Embed:
        gid, uid = str(member.guild.id), str(member.id)
        row = self.bot.store.get_xp(gid, uid)
        level, into, need = level_info(row["xp"])
        embed = discord.Embed(title=tr("{name} · רמה {n}", cfg.get("_lang", "he"), name=member.display_name, n=level), color=parse_color(cfg["card_color"]))
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=tr("דירוג", cfg.get("_lang", "he")), value=f"#{self.bot.store.rank_of(gid, uid)}" if row["xp"] else "—")
        embed.add_field(name=tr("XP כולל", cfg.get("_lang", "he")), value=f"{row['xp']:,}")
        embed.add_field(name=tr("הודעות", cfg.get("_lang", "he")), value=f"{row['messages']:,}")
        embed.add_field(name=tr("לרמה {n}", cfg.get("_lang", "he"), n=level + 1), value=f"{progress_bar(into, need)}  {into:,} / {need:,} XP", inline=False)
        return embed

    def _off(self, interaction: discord.Interaction) -> dict | None:
        return self.bot.cfg(interaction.guild, "levels")

    @app_commands.command(name="rank", description=N_("הרמה וה-XP שלך (או של חבר אחר)"))
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None):
        cfg = self._off(interaction)
        if not cfg:
            return await interaction.response.send_message(tr("מערכת הרמות כבויה בשרת הזה.", self.bot.lang(interaction.guild)), ephemeral=True)
        await interaction.response.send_message(embed=self._rank_embed(member or interaction.user, cfg))

    @app_commands.command(name="levels", description=N_("לוח המובילים של השרת"))
    @app_commands.guild_only()
    async def levels(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 100] = 1):
        cfg = self._off(interaction)
        if not cfg:
            return await interaction.response.send_message(tr("מערכת הרמות כבויה בשרת הזה.", self.bot.lang(interaction.guild)), ephemeral=True)
        rows = self.bot.store.leaderboard(str(interaction.guild_id), limit=10, offset=(page - 1) * 10)
        if not rows:
            return await interaction.response.send_message(tr("עוד אין אף אחד בלוח.", cfg.get("_lang", "he")), ephemeral=True)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, r in enumerate(rows, start=(page - 1) * 10 + 1):
            lines.append(f"{medals.get(i, f'`#{i}`')} <@{r['user_id']}> · " + tr("רמה **{n}**", cfg.get("_lang", "he"), n=level_info(r['xp'])[0]) + f" · {r['xp']:,} XP")
        embed = discord.Embed(title=tr("🏆 לוח המובילים · {server}", cfg.get("_lang", "he"), server=interaction.guild.name), description="\n".join(lines),
                              color=parse_color(cfg["card_color"]))
        embed.set_footer(text=tr("עמוד {n}", cfg.get("_lang", "he"), n=page))
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def _manual(self, interaction: discord.Interaction, member: discord.Member, delta: int):
        cfg = self._off(interaction)
        if not cfg:
            return await interaction.response.send_message(tr("מערכת הרמות כבויה בשרת הזה.", self.bot.lang(interaction.guild)), ephemeral=True)
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message(tr("אין לך הרשאה לפעולה הזו.", cfg.get("_lang", "he")), ephemeral=True)
        if member.bot:
            return await interaction.response.send_message(tr("לבוטים אין XP.", cfg.get("_lang", "he")), ephemeral=True)
        old, new = self.bot.store.change_xp(str(interaction.guild_id), str(member.id), str(member), delta)
        await interaction.response.send_message(
            tr("✅ ל-{user} יש עכשיו {xp} XP (רמה {n}).", cfg.get("_lang", "he"), user=member.mention, xp=f"{new:,}", n=level_info(new)[0]), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())
        await self.apply_change(member, old, new, cfg, interaction.channel)
        log.info(tr("%s שינה ל-%s את ה-XP ב-%+d"), interaction.user, member, new - old)

    @app_commands.command(name="give-xp", description=N_("הוספת XP לחבר"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def give_xp(self, interaction: discord.Interaction, member: discord.Member,
                      amount: app_commands.Range[int, 1, 1_000_000]):
        await self._manual(interaction, member, amount)

    @app_commands.command(name="remove-xp", description=N_("הורדת XP מחבר"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def remove_xp(self, interaction: discord.Interaction, member: discord.Member,
                        amount: app_commands.Range[int, 1, 1_000_000]):
        await self._manual(interaction, member, -amount)
