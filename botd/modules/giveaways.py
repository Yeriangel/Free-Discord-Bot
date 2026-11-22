"""Giveaways: a message with a join button; when time is up the bot draws random winners."""

from ..i18n import N_, tr
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .common import has_any_role, parse_duration, relative_time, reply_off

log = logging.getLogger("botd.giveaways")
_rng = random.SystemRandom()
MAX_SECONDS = 60 * 86400


def draw(entries: list[str], count: int, exclude: set[str] = frozenset()) -> list[str]:
    pool = [e for e in entries if e not in exclude]
    return _rng.sample(pool, min(count, len(pool)))


def giveaway_embed(gw: dict, lang: str, entries: int | None = None, winners: list[str] | None = None) -> discord.Embed:
    lines = []
    if winners is None:
        lines.append(tr("לחץ על הכפתור כדי להשתתף!", lang))
        lines.append(tr("מסתיים: {when}", lang, when=relative_time(gw["ends"])))
    else:
        lines.append(tr("🏆 זוכים: {list}", lang, list=", ".join(f"<@{w}>" for w in winners) or tr("אין משתתפים", lang)))
    lines.append(tr("מספר זוכים: {n}", lang, n=gw["winners"]))
    if gw.get("role_id"):
        lines.append(tr("רק למי שיש לו: {role}", lang, role=f"<@&{gw['role_id']}>"))
    if gw.get("host_id"):
        lines.append(tr("מארגן: {user}", lang, user=f"<@{gw['host_id']}>"))
    if entries is not None:
        lines.append(tr("משתתפים: {n}", lang, n=entries))
    embed = discord.Embed(title=f"🎉 {gw['prize']}", description="\n".join(lines),
                          color=0x2FBF71 if winners is None else 0x5865F2)
    embed.set_footer(text=f"#{gw['id']}" + (" · " + tr("ההגרלה הסתיימה", lang) if winners is not None else ""))
    return embed


class JoinView(discord.ui.View):
    def __init__(self, cog, label: str = "🎉"):
        super().__init__(timeout=None)
        self.cog = cog
        self.join.label = label[:80]

    @discord.ui.button(label="🎉", style=discord.ButtonStyle.success, custom_id="giveaway:join")
    async def join(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.cog.on_join(interaction)


class Giveaways(commands.Cog):
    giveaway = app_commands.Group(name="giveaway", description=N_("הגרלות"), guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        self.loop.start()

    async def cog_load(self) -> None:
        self.bot.add_view(JoinView(self))

    async def cog_unload(self) -> None:
        self.loop.cancel()

    def _manager(self, member: discord.Member, cfg: dict) -> bool:
        return member.guild_permissions.manage_guild or has_any_role(member, cfg["manager_role_ids"])

    async def start(self, guild: discord.Guild, channel, host: discord.abc.User, prize: str, seconds: int, winners: int,
                    role_id: str = "") -> dict:
        cfg = self.bot.cfg(guild, "giveaways")
        if not cfg:
            raise ValueError(tr("מודול ההגרלות כבוי בשרת הזה"))
        gid = self.bot.store.add_giveaway(str(guild.id), str(channel.id), prize, winners, time.time() + seconds,
                                          str(host.id), role_id)
        gw = self.bot.store.get_giveaway(gid)
        msg = await channel.send(embed=giveaway_embed(gw, cfg["_lang"], 0), view=JoinView(self, cfg["button_label"] or "🎉"),
                                 allowed_mentions=discord.AllowedMentions.none())
        self.bot.store.set_giveaway_message(gid, str(msg.id))
        gw["message_id"] = str(msg.id)
        return gw

    async def on_join(self, interaction: discord.Interaction) -> None:
        gw = self.bot.store.giveaway_by_message(str(interaction.message.id)) if interaction.message else None
        lang = self.bot.lang(interaction.guild)
        if gw is None or gw["ended"]:
            return await interaction.response.send_message(tr("ההגרלה הזו כבר הסתיימה.", lang), ephemeral=True)
        if gw["role_id"] and not has_any_role(interaction.user, [gw["role_id"]]):
            return await interaction.response.send_message(tr("ההגרלה הזו רק למי שיש לו את התפקיד הנדרש.", lang), ephemeral=True)
        joined = self.bot.store.toggle_entry(gw["id"], str(interaction.user.id))
        count = len(self.bot.store.giveaway_entries(gw["id"]))
        await interaction.response.send_message(
            tr("✅ נרשמת להגרלה! (משתתפים: {n}). לחיצה נוספת מבטלת.", lang, n=count) if joined
            else tr("יצאת מההגרלה. (משתתפים: {n})", lang, n=count), ephemeral=True)

    @tasks.loop(seconds=15)
    async def loop(self) -> None:
        for gw in self.bot.store.due_giveaways(time.time()):
            try:
                await self.finish(gw)
            except Exception:  # noqa: BLE001
                log.exception(tr("סיום הגרלה נכשל"))
                self.bot.store.end_giveaway(gw["id"], [])

    @loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    async def finish(self, gw: dict, exclude: set[str] = frozenset()) -> list[str]:
        guild = self.bot.get_guild(int(gw["guild_id"]))
        lang = self.bot.lang(guild)
        entries = self.bot.store.giveaway_entries(gw["id"])
        if guild is not None and self.bot.intents.members:
            entries = [e for e in entries if guild.get_member(int(e))]  # people who left cannot win
        winners = draw(entries, gw["winners"], exclude)
        self.bot.store.end_giveaway(gw["id"], winners)
        channel = guild.get_channel(int(gw["channel_id"])) if guild else None
        if channel is None:
            return winners
        try:
            msg = await channel.fetch_message(int(gw["message_id"]))
            await msg.edit(embed=giveaway_embed(gw, lang, len(entries), winners), view=None)
        except (discord.HTTPException, ValueError):
            msg = None
        if winners:
            text = tr("🎉 מזל טוב {list}! זכיתם ב-**{prize}**!", lang, list=", ".join(f"<@{w}>" for w in winners), prize=gw["prize"])
        else:
            text = tr("ההגרלה על **{prize}** הסתיימה בלי משתתפים.", lang, prize=gw["prize"])
        await channel.send(text, reference=msg, mention_author=False, allowed_mentions=discord.AllowedMentions(users=True))
        cfg = self.bot.cfg(guild, "giveaways")
        if cfg and cfg["dm_winners"]:
            for w in winners:
                member = guild.get_member(int(w))
                if member is not None:
                    try:
                        await member.send(tr("🎉 זכית ב-**{prize}** בשרת {server}!", lang, prize=gw["prize"], server=guild.name))
                    except discord.HTTPException:
                        pass
        return winners

    @giveaway.command(name="start", description=N_("התחלת הגרלה"))
    @app_commands.describe(prize=N_("הפרס"), duration=N_("כמה זמן: 10m, 2h, 1d"), winners=N_("כמה זוכים"),
                           channel=N_("באיזה ערוץ (ברירת מחדל: כאן)"), role=N_("רק לבעלי התפקיד הזה (לא חובה)"))
    async def start_cmd(self, interaction: discord.Interaction, prize: app_commands.Range[str, 1, 200], duration: str,
                        winners: app_commands.Range[int, 1, 20] = 1, channel: discord.TextChannel | None = None,
                        role: discord.Role | None = None):
        cfg = self.bot.cfg(interaction.guild, "giveaways")
        if not cfg:
            return await reply_off(interaction, self.bot)
        lang = cfg["_lang"]
        if not self._manager(interaction.user, cfg):
            return await interaction.response.send_message(tr("אין לך הרשאה ליצור הגרלות.", lang), ephemeral=True)
        seconds = parse_duration(duration)
        if not seconds or not 60 <= seconds <= MAX_SECONDS:
            return await interaction.response.send_message(tr("משך לא תקין. כתוב למשל 30m, 2h או 3d (עד 60 יום).", lang),
                                                           ephemeral=True)
        try:
            gw = await self.start(interaction.guild, channel or interaction.channel, interaction.user, prize, seconds, winners,
                                  str(role.id) if role else "")
        except discord.HTTPException as exc:
            return await interaction.response.send_message(str(exc.text or exc.status), ephemeral=True)
        await interaction.response.send_message(tr("🎉 הגרלה #{id} התחילה.", lang, id=gw["id"]), ephemeral=True)

    async def _own(self, interaction: discord.Interaction, gw_id: int) -> tuple[dict | None, dict | None]:
        cfg = self.bot.cfg(interaction.guild, "giveaways")
        if not cfg:
            await reply_off(interaction, self.bot)
            return None, None
        gw = self.bot.store.get_giveaway(gw_id)
        if gw is None or gw["guild_id"] != str(interaction.guild_id):
            await interaction.response.send_message(tr("ההגרלה לא נמצאה.", cfg["_lang"]), ephemeral=True)
            return None, None
        if not self._manager(interaction.user, cfg):
            await interaction.response.send_message(tr("אין לך הרשאה לנהל הגרלות.", cfg["_lang"]), ephemeral=True)
            return None, None
        return cfg, gw

    @giveaway.command(name="end", description=N_("סיום הגרלה עכשיו"))
    async def end_cmd(self, interaction: discord.Interaction, giveaway_id: int):
        cfg, gw = await self._own(interaction, giveaway_id)
        if gw is None:
            return
        if gw["ended"]:
            return await interaction.response.send_message(tr("ההגרלה כבר הסתיימה.", cfg["_lang"]), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self.finish(gw)
        await interaction.followup.send("✅", ephemeral=True)

    @giveaway.command(name="reroll", description=N_("הגרלת זוכה חדש"))
    async def reroll_cmd(self, interaction: discord.Interaction, giveaway_id: int):
        cfg, gw = await self._own(interaction, giveaway_id)
        if gw is None:
            return
        if not gw["ended"]:
            return await interaction.response.send_message(tr("ההגרלה עוד לא הסתיימה.", cfg["_lang"]), ephemeral=True)
        import json

        previous = set(json.loads(gw["winner_ids"] or "[]"))
        await interaction.response.defer(ephemeral=True)
        await self.finish({**gw, "winners": 1}, exclude=previous)
        await interaction.followup.send("✅", ephemeral=True)

    @giveaway.command(name="list", description=N_("ההגרלות הפעילות"))
    async def list_cmd(self, interaction: discord.Interaction):
        cfg = self.bot.cfg(interaction.guild, "giveaways")
        if not cfg:
            return await reply_off(interaction, self.bot)
        rows = [g for g in self.bot.store.list_giveaways(str(interaction.guild_id)) if not g["ended"]]
        if not rows:
            return await interaction.response.send_message(tr("אין הגרלות פעילות.", cfg["_lang"]), ephemeral=True)
        lines = [f"`#{g['id']}` **{discord.utils.escape_markdown(g['prize'])}** · {relative_time(g['ends'])} · 👥 {g['entries']}"
                 for g in rows[:15]]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
