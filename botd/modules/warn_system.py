"""Warning system with automated actions (timeout / kick / temp-ban / ban when a count is reached)."""

import logging
import time
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..i18n import N_, tr
from .common import COLOR_OK, COLOR_WARN, mod_embed, themed
from .moderation import ask_confirm

log = logging.getLogger("botd.warnings")

ACTION_LABELS = {"timeout": N_("השתקה זמנית"), "kick": N_("הרחקה"), "tempban": N_("חסימה זמנית"), "ban": N_("חסימה")}


def pick_step(steps: list[dict], total: int) -> dict | None:
    """The automated action for reaching exactly `total` warnings (each threshold fires once)."""
    return next((s for s in steps if s["count"] == total), None)


def can_punish(guild: discord.Guild, member: discord.Member) -> bool:
    me = guild.me
    return member.id != guild.owner_id and member.id != me.id and member.top_role < me.top_role


class Warnings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.unban_loop.start()

    async def cog_unload(self) -> None:
        self.unban_loop.cancel()

    # ---- core, also used by AutoMod -----------------------------------------
    async def add_warning(self, guild: discord.Guild, member: discord.Member, reason: str,
                          moderator: discord.abc.User | None = None) -> tuple[int, int, str] | None:
        """Records a warning and runs the automated action. Returns (id, total, action text) or None if off."""
        cfg = self.bot.cfg(guild, "warnings")
        if not cfg:
            return None
        lang = self.bot.lang(guild)
        gid, uid = str(guild.id), str(member.id)
        wid = self.bot.store.add_warning(gid, uid, str(member), str(moderator.id) if moderator else "",
                                         str(moderator) if moderator else tr("אוטומוד", lang), reason)
        since = time.time() - cfg["window_days"] * 86400 if cfg["window_days"] else 0
        total = self.bot.store.count_warnings(gid, uid, since)
        if cfg["dm_user"]:
            try:
                await member.send(tr("⚠️ קיבלת אזהרה בשרת **{server}**: {reason}", lang,
                                     server=discord.utils.escape_markdown(guild.name), reason=reason)
                                  + "\n" + tr("סך הכל אזהרות: {n}", lang, n=total))
            except discord.HTTPException:
                pass
        action_text = await self._escalate(guild, member, pick_step(cfg["steps"], total), total, lang)
        extra = tr("סך הכל אזהרות: {n}", lang, n=total)
        if action_text:
            extra += "\n" + tr("פעולה אוטומטית: {action}", lang, action=action_text)
        await self.bot.send_log(guild, mod_embed(tr("⚠️ אזהרה #{id}", lang, id=wid), member, moderator, reason,
                                                 COLOR_WARN, extra=extra, lang=lang), "mod_actions")
        log.info(tr("אזהרה ל-%s בשרת %s (סך הכל %d)"), member, guild.name, total)
        return wid, total, action_text

    async def _escalate(self, guild: discord.Guild, member: discord.Member, step: dict | None, total: int,
                        lang: str = "he") -> str:
        if step is None:
            return ""
        if not can_punish(guild, member):
            log.warning(tr("פעולה אוטומטית דולגה: התפקיד של %s גבוה מתפקיד הבוט"), member)
            return tr("דולגה (התפקיד של המשתמש גבוה מתפקיד הבוט)", lang)
        reason = tr("פעולה אוטומטית אחרי {n} אזהרות", lang, n=total)
        action, minutes = step["action"], step["minutes"]
        try:
            if action == "timeout":
                await member.timeout(timedelta(minutes=minutes), reason=reason)
                return tr("השתקה ל-{n} דקות", lang, n=minutes)
            if action == "kick":
                await member.kick(reason=reason)
                return tr("הרחקה", lang)
            if action in ("ban", "tempban"):
                await guild.ban(member, reason=reason, delete_message_seconds=0)
                if action == "tempban":
                    self.bot.store.add_tempban(str(guild.id), str(member.id), time.time() + minutes * 60)
                    return tr("חסימה ל-{n} דקות", lang, n=minutes)
                return tr("חסימה", lang)
        except discord.HTTPException as exc:
            log.warning(tr("פעולה אוטומטית (%s) נכשלה: %s"), action, exc.text or exc.status)
            return tr("{action} נכשלה: {error}", lang, action=tr(ACTION_LABELS[action], lang), error=exc.text or exc.status)
        return ""

    @tasks.loop(seconds=60)
    async def unban_loop(self) -> None:
        for row in self.bot.store.due_tempbans(time.time()):
            guild = self.bot.get_guild(int(row["guild_id"]))
            if guild is None or not self.bot.manager.owns(row["guild_id"]):
                continue  # not connected to that server right now (or another of your bots runs it); try again later
            try:
                await guild.unban(discord.Object(int(row["user_id"])), reason=tr("סוף חסימה זמנית", self.bot.lang(guild)))
                log.info(tr("חסימה זמנית הסתיימה למשתמש %s בשרת %s"), row["user_id"], guild.name)
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                log.warning(tr("ביטול חסימה זמנית נכשל: %s"), exc.text or exc.status)
                continue
            self.bot.store.remove_tempban(row["guild_id"], row["user_id"])

    @unban_loop.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ---- slash commands -------------------------------------------------------
    async def _check(self, interaction: discord.Interaction) -> str | None:
        lang = self.bot.lang(interaction.guild)
        if not self.bot.cfg(interaction.guild, "warnings"):
            return tr("מערכת האזהרות כבויה בשרת הזה. אפשר להפעיל אותה באפליקציה.", lang)
        if not interaction.user.guild_permissions.moderate_members:
            return tr("אין לך הרשאה לפעולה הזו.", lang)
        return None

    @app_commands.command(name="warn", description=N_("מתן אזהרה למשתמש"))
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member=N_("למי"), reason=N_("סיבה"))
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        lang = self.bot.lang(interaction.guild)
        if error := await self._check(interaction):
            return await interaction.response.send_message(error, ephemeral=True)
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message(tr("אי אפשר לתת אזהרה לבוט או לעצמך.", lang), ephemeral=True)
        await interaction.response.defer()
        wid, total, action = await self.add_warning(interaction.guild, member, reason, interaction.user)
        # said in the channel, so the member (and everyone) sees the warning and the reason
        text = tr("⚠️ {user} קיבל אזהרה (#{id}). סיבה: {reason}. סך הכל: {n}.", lang, id=wid, user=member.mention,
                  reason=discord.utils.escape_markdown(reason[:500]), n=total)
        if action:
            text += "\n" + tr("פעולה אוטומטית: {action}", lang, action=action)
        await interaction.followup.send(text, allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False))

    @app_commands.command(name="warnings", description=N_("רשימת האזהרות של משתמש"))
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def warnings_cmd(self, interaction: discord.Interaction, member: discord.Member):
        lang = self.bot.lang(interaction.guild)
        if error := await self._check(interaction):
            return await interaction.response.send_message(error, ephemeral=True)
        rows = self.bot.store.list_warnings(str(interaction.guild_id), str(member.id), limit=15)
        if not rows:
            return await interaction.response.send_message(tr("ל-{user} אין אזהרות.", lang, user=member), ephemeral=True)
        embed = discord.Embed(title=tr("אזהרות של {user}", lang, user=member), color=themed(lang, COLOR_WARN))
        for r in rows:
            embed.add_field(name=f"#{r['id']} · <t:{int(r['created'])}:R>",
                            value=r["reason"][:200] + "\n" + tr("על ידי: {name}", lang, name=r["moderator_name"]), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="unwarn", description=N_("מחיקת אזהרה לפי מספר (עם אישור)"))
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def unwarn(self, interaction: discord.Interaction, warning_id: int):
        lang = self.bot.lang(interaction.guild)
        if error := await self._check(interaction):
            return await interaction.response.send_message(error, ephemeral=True)
        if not await ask_confirm(interaction, tr("למחוק את אזהרה #{id}?", lang, id=warning_id), "", lang):
            return
        ok = self.bot.store.delete_warning(str(interaction.guild_id), warning_id)
        await interaction.edit_original_response(content=tr("✅ האזהרה נמחקה.", lang) if ok else tr("האזהרה לא נמצאה.", lang))
        if ok:
            await self.bot.send_log(interaction.guild, mod_embed(tr("🗑 אזהרה #{id} נמחקה", lang, id=warning_id), interaction.user,
                                                                 interaction.user, None, COLOR_OK, lang=lang), "mod_actions")

    @app_commands.command(name="clearwarns", description=N_("מחיקת כל האזהרות של משתמש (עם אישור)"))
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member):
        lang = self.bot.lang(interaction.guild)
        if error := await self._check(interaction):
            return await interaction.response.send_message(error, ephemeral=True)
        if not await ask_confirm(interaction, tr("למחוק את כל האזהרות של {user}?", lang, user=member), "", lang):
            return
        n = self.bot.store.clear_warnings(str(interaction.guild_id), str(member.id))
        await interaction.edit_original_response(content=tr("✅ נמחקו {n} אזהרות.", lang, n=n))
        await self.bot.send_log(interaction.guild, mod_embed(tr("🗑 כל האזהרות נמחקו", lang), member, interaction.user, None,
                                                             COLOR_OK, extra=tr("{n} אזהרות", lang, n=n), lang=lang), "mod_actions")
