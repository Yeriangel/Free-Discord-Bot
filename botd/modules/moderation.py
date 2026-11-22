"""Moderation slash commands. Kick, ban, timeout and purge always ask for confirmation first."""

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from ..i18n import N_, tr
from .common import COLOR_DANGER, COLOR_OK, COLOR_WARN, mod_embed, themed

log = logging.getLogger("botd.moderation")


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, lang: str = "he"):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.lang = lang
        self.value: bool | None = None
        self.interaction: discord.Interaction | None = None
        self.confirm.label = tr("אשר", lang)
        self.cancel.label = tr("ביטול", lang)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(tr("רק מי שהפעיל את הפקודה יכול לאשר.", self.lang), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="OK", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.value, self.interaction = True, interaction
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.value, self.interaction = False, interaction
        self.stop()


async def ask_confirm(interaction: discord.Interaction, title: str, description: str, lang: str) -> bool:
    """Ephemeral confirm dialog with buttons. Returns True if the invoker pressed confirm."""
    view = ConfirmView(interaction.user.id, lang)
    embed = discord.Embed(title=title, description=description or None, color=themed(lang, COLOR_WARN))
    embed.set_footer(text=tr("הבקשה תפוג בעוד 30 שניות", lang))
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    await view.wait()
    if view.value is None:
        await interaction.edit_original_response(content=tr("⌛ פג תוקף. לא בוצע דבר.", lang), embed=None, view=None)
        return False
    if not view.value:
        await view.interaction.response.edit_message(content=tr("בוטל. לא בוצע דבר.", lang), embed=None, view=None)
        return False
    await view.interaction.response.edit_message(content=tr("מבצע…", lang), embed=None, view=None)
    return True


def hierarchy_error(guild: discord.Guild, invoker: discord.Member, target: discord.Member, lang: str = "he") -> str | None:
    me = guild.me
    if target.id == invoker.id:
        return tr("אי אפשר לבצע את הפעולה על עצמך.", lang)
    if target.id == guild.owner_id:
        return tr("אי אפשר לבצע את הפעולה על בעל השרת.", lang)
    if target.id == me.id:
        return tr("אי אפשר לבצע את הפעולה על הבוט עצמו.", lang)
    if invoker.id != guild.owner_id and target.top_role >= invoker.top_role:
        return tr("התפקיד של המשתמש גבוה משלך או שווה לו.", lang)
    if target.top_role >= me.top_role:
        return tr("התפקיד של המשתמש גבוה מתפקיד הבוט. הזז את תפקיד הבוט למעלה בהגדרות השרת.", lang)
    return None


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _l(self, interaction: discord.Interaction) -> str:
        return self.bot.lang(interaction.guild)

    async def _guard(self, interaction: discord.Interaction, perm: str, target=None) -> str | None:
        guild, lang = interaction.guild, self._l(interaction)
        if not self.bot.cfg(guild, "mod_commands"):
            return tr("פקודות הניהול כבויות בשרת הזה. אפשר להפעיל אותן באפליקציה.", lang)
        invoker = interaction.user
        if not isinstance(invoker, discord.Member) or not getattr(invoker.guild_permissions, perm):
            return tr("אין לך הרשאה לפעולה הזו.", lang)
        if not getattr(guild.me.guild_permissions, perm):
            return tr("לבוט אין הרשאה לפעולה הזו. צור קישור הזמנה חדש עם ההרשאה בעמוד 'הרשאות והזמנה'.", lang)
        if isinstance(target, discord.Member):
            return hierarchy_error(guild, invoker, target, lang)
        return None

    async def _done(self, interaction: discord.Interaction, text: str) -> None:
        await interaction.edit_original_response(content=text, embed=None, view=None)

    async def _dm(self, user: discord.abc.User, guild: discord.Guild, kicked: bool, reason: str | None) -> None:
        if not (self.bot.cfg(guild, "mod_commands") or {}).get("dm_user"):
            return
        lang = self.bot.lang(guild)
        server = discord.utils.escape_markdown(guild.name)
        text = tr("הורחקת מהשרת **{server}**.", lang, server=server) if kicked else tr("נחסמת מהשרת **{server}**.", lang, server=server)
        if reason:
            text += "\n" + tr("סיבה: {reason}", lang, reason=reason)
        try:
            await user.send(text)
        except discord.HTTPException:
            pass

    @staticmethod
    def _audit_reason(invoker: discord.abc.User, reason: str | None, lang: str) -> str:
        return f"{invoker} ({invoker.id}): {reason or tr('ללא סיבה', lang)}"[:500]

    def _reason_line(self, reason: str | None, lang: str) -> str:
        return tr("סיבה: {reason}", lang, reason=reason or "—")

    # ---- commands -----------------------------------------------------------
    @app_commands.command(name="kick", description=N_("הרחקת חבר מהשרת (עם אישור)"))
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @app_commands.describe(member=N_("מי להרחיק"), reason=N_("סיבה"))
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None):
        lang = self._l(interaction)
        if error := await self._guard(interaction, "kick_members", member):
            return await interaction.response.send_message(error, ephemeral=True)
        if not await ask_confirm(interaction, tr("להרחיק את {user}?", lang, user=member), self._reason_line(reason, lang), lang):
            return
        await self._dm(member, interaction.guild, True, reason)
        try:
            await member.kick(reason=self._audit_reason(interaction.user, reason, lang))
        except discord.HTTPException as exc:
            return await self._done(interaction, tr("❌ ההרחקה נכשלה: {error}", lang, error=exc.text or exc.status))
        await self._done(interaction, tr("✅ {user} הורחק.", lang, user=member))
        log.info(tr("%s הרחיק את %s בשרת %s"), interaction.user, member, interaction.guild.name)
        await self.bot.send_log(interaction.guild, mod_embed(tr("👢 הרחקה", lang), member, interaction.user, reason,
                                                             lang=lang), "mod_actions")

    @app_commands.command(name="ban", description=N_("חסימת משתמש (עם אישור)"))
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(user=N_("מי לחסום"), reason=N_("סיבה"), delete_days=N_("למחוק הודעות מהימים האחרונים (0–7)"))
    async def ban(self, interaction: discord.Interaction, user: discord.User,
                  reason: str | None = None, delete_days: app_commands.Range[int, 0, 7] = 0):
        lang = self._l(interaction)
        target = interaction.guild.get_member(user.id) or user
        if error := await self._guard(interaction, "ban_members", target):
            return await interaction.response.send_message(error, ephemeral=True)
        desc = self._reason_line(reason, lang) + "\n" + tr("מחיקת הודעות: {n} ימים אחרונים", lang, n=delete_days)
        if not await ask_confirm(interaction, tr("לחסום את {user}?", lang, user=user), desc, lang):
            return
        if isinstance(target, discord.Member):
            await self._dm(target, interaction.guild, False, reason)
        try:
            await interaction.guild.ban(user, reason=self._audit_reason(interaction.user, reason, lang),
                                        delete_message_seconds=delete_days * 86400)
        except discord.HTTPException as exc:
            return await self._done(interaction, tr("❌ החסימה נכשלה: {error}", lang, error=exc.text or exc.status))
        await self._done(interaction, tr("✅ {user} נחסם.", lang, user=user))
        log.info(tr("%s חסם את %s בשרת %s"), interaction.user, user, interaction.guild.name)
        await self.bot.send_log(interaction.guild, mod_embed(tr("⛔ חסימה", lang), user, interaction.user, reason,
                                                             lang=lang), "mod_actions")

    @app_commands.command(name="unban", description=N_("ביטול חסימה לפי מזהה משתמש"))
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(user_id=N_("מזהה המשתמש"), reason=N_("סיבה"))
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str | None = None):
        lang = self._l(interaction)
        if error := await self._guard(interaction, "ban_members"):
            return await interaction.response.send_message(error, ephemeral=True)
        if not user_id.strip().isdigit():
            return await interaction.response.send_message(tr("מזהה לא תקין.", lang), ephemeral=True)
        try:
            await interaction.guild.unban(discord.Object(int(user_id)), reason=self._audit_reason(interaction.user, reason, lang))
        except discord.NotFound:
            return await interaction.response.send_message(tr("המשתמש הזה לא חסום.", lang), ephemeral=True)
        except discord.HTTPException as exc:
            return await interaction.response.send_message(tr("❌ נכשל: {error}", lang, error=exc.text or exc.status), ephemeral=True)
        await interaction.response.send_message(tr("✅ החסימה של `{id}` בוטלה.", lang, id=user_id), ephemeral=True)
        try:
            user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
        except discord.HTTPException:
            return
        await self.bot.send_log(interaction.guild, mod_embed(tr("✅ ביטול חסימה", lang), user, interaction.user, reason,
                                                             COLOR_OK, lang=lang), "mod_actions")

    @app_commands.command(name="timeout", description=N_("השתקה זמנית (עם אישור)"))
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member=N_("את מי להשתיק"), minutes=N_("כמה דקות (עד 28 ימים)"), reason=N_("סיבה"))
    async def timeout(self, interaction: discord.Interaction, member: discord.Member,
                      minutes: app_commands.Range[int, 1, 40320], reason: str | None = None):
        lang = self._l(interaction)
        if error := await self._guard(interaction, "moderate_members", member):
            return await interaction.response.send_message(error, ephemeral=True)
        if not await ask_confirm(interaction, tr("להשתיק את {user} ל-{n} דקות?", lang, user=member, n=minutes),
                                 self._reason_line(reason, lang), lang):
            return
        try:
            await member.timeout(timedelta(minutes=minutes), reason=self._audit_reason(interaction.user, reason, lang))
        except discord.HTTPException as exc:
            return await self._done(interaction, tr("❌ ההשתקה נכשלה: {error}", lang, error=exc.text or exc.status))
        await self._done(interaction, tr("✅ {user} מושתק ל-{n} דקות.", lang, user=member, n=minutes))
        await self.bot.send_log(interaction.guild, mod_embed(tr("🔇 השתקה", lang), member, interaction.user, reason, COLOR_WARN,
                                                             extra=tr("{n} דקות", lang, n=minutes), lang=lang), "mod_actions")

    @app_commands.command(name="untimeout", description=N_("ביטול השתקה"))
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        lang = self._l(interaction)
        if error := await self._guard(interaction, "moderate_members", member):
            return await interaction.response.send_message(error, ephemeral=True)
        try:
            await member.timeout(None, reason=self._audit_reason(interaction.user, tr("ביטול השתקה", lang), lang))
        except discord.HTTPException as exc:
            return await interaction.response.send_message(tr("❌ נכשל: {error}", lang, error=exc.text or exc.status), ephemeral=True)
        await interaction.response.send_message(tr("✅ ההשתקה של {user} בוטלה.", lang, user=member), ephemeral=True)
        await self.bot.send_log(interaction.guild, mod_embed(tr("🔊 ביטול השתקה", lang), member, interaction.user, None,
                                                             COLOR_OK, lang=lang), "mod_actions")

    @app_commands.command(name="purge", description=N_("מחיקת הודעות אחרונות בערוץ (עם אישור)"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(amount=N_("כמה הודעות לבדוק (1–100)"), member=N_("רק הודעות של המשתמש הזה (לא חובה)"))
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100],
                    member: discord.Member | None = None):
        lang = self._l(interaction)
        if error := await self._guard(interaction, "manage_messages"):
            return await interaction.response.send_message(error, ephemeral=True)
        title = tr("למחוק הודעות של {user}?", lang, user=member) if member else tr("למחוק הודעות?", lang)
        desc = tr("ייבדקו {n} ההודעות האחרונות ב-{channel}. אי אפשר לבטל.", lang, n=amount, channel=interaction.channel.mention)
        if not await ask_confirm(interaction, title, desc, lang):
            return
        try:
            deleted = await interaction.channel.purge(
                limit=amount, check=(lambda m: m.author.id == member.id) if member else None,
                reason=self._audit_reason(interaction.user, "purge", lang))
        except discord.HTTPException as exc:
            return await self._done(interaction, tr("❌ המחיקה נכשלה: {error}", lang, error=exc.text or exc.status))
        await self._done(interaction, tr("✅ נמחקו {n} הודעות.", lang, n=len(deleted)))
        await self.bot.send_log(interaction.guild, mod_embed(
            tr("🧹 מחיקת הודעות", lang), member or interaction.user, interaction.user, None, COLOR_DANGER,
            extra=tr("{n} הודעות ב-{channel}", lang, n=len(deleted), channel=interaction.channel.mention), lang=lang), "mod_actions")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.error(tr("פקודת ניהול נכשלה: %s"), error)
        text = tr("אירעה שגיאה. הפרטים נמצאים בלוג של האפליקציה.", self._l(interaction))
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(content=text, embed=None, view=None)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass
