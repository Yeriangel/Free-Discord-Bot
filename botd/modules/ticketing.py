"""Support tickets: a panel with a button opens a private thread between the member and the staff roles."""

from ..i18n import N_, tr
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .common import COLOR_INFO, has_any_role, reply_off, text_channel, themed
from .welcome import parse_color

log = logging.getLogger("botd.tickets")


class TicketPanelView(discord.ui.View):
    def __init__(self, cog, label: str = "🎫"):
        super().__init__(timeout=None)
        self.cog = cog
        self.open_button.label = label[:80]

    @discord.ui.button(label="🎫", style=discord.ButtonStyle.primary, custom_id="ticket:open")
    async def open_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.cog.open_ticket(interaction)


class TicketCloseView(discord.ui.View):
    def __init__(self, cog, label: str = "🔒"):
        super().__init__(timeout=None)
        self.cog = cog
        self.close_button.label = label[:80]

    @discord.ui.button(label="🔒", style=discord.ButtonStyle.danger, custom_id="ticket:close")
    async def close_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.cog.close_ticket(interaction)


class Ticketing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(TicketPanelView(self))
        self.bot.add_view(TicketCloseView(self))

    async def publish_panel(self, guild: discord.Guild) -> discord.Message:
        cfg = self.bot.cfg(guild, "ticketing")
        if not cfg:
            raise ValueError(tr("המודול כבוי בשרת הזה"))
        channel = text_channel(guild, cfg["panel_channel_id"])
        if channel is None:
            raise ValueError(tr("בחר ערוץ לכפתור ושמור"))
        embed = discord.Embed(title=cfg["panel_title"][:256] or None, description=cfg["panel_message"][:4000] or None,
                              color=themed(cfg["_lang"], COLOR_INFO))
        return await channel.send(embed=embed, view=TicketPanelView(self, cfg["button_label"] or "🎫"))

    async def open_ticket(self, interaction: discord.Interaction) -> None:
        guild, member = interaction.guild, interaction.user
        cfg = self.bot.cfg(guild, "ticketing")
        if not cfg:
            return await reply_off(interaction, self.bot)
        lang = cfg["_lang"]
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(tr("אפשר לפתוח כרטיס רק מערוץ טקסט.", lang), ephemeral=True)
        gid = str(guild.id)
        open_now = [t for t in self.bot.store.open_tickets_of(gid, str(member.id)) if guild.get_thread(int(t["thread_id"]))]
        if len(open_now) >= cfg["max_open"]:
            link = f"<#{open_now[0]['thread_id']}>"
            return await interaction.response.send_message(tr("כבר יש לך כרטיס פתוח: {link}", lang, link=link), ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        number = self.bot.store.next_ticket_number(gid)
        try:
            thread = await channel.create_thread(name=f"🎫 {number:04d} · {member.display_name}"[:100],
                                                 type=discord.ChannelType.private_thread, invitable=False,
                                                 reason=f"ticket #{number}")
            await thread.add_user(member)
            roles = " ".join(f"<@&{r}>" for r in cfg["support_role_ids"])
            welcome = cfg["welcome_message"].replace("{user}", member.mention).replace("{number}", str(number))
            await thread.send(f"{welcome}\n{roles}".strip()[:2000], view=TicketCloseView(self, tr("סגור כרטיס", lang)),
                              allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False))
        except discord.HTTPException as exc:
            log.warning(tr("פתיחת כרטיס נכשלה בשרת %s: %s"), guild.name, exc.text or exc.status)
            return await interaction.followup.send(tr("לא הצלחתי לפתוח כרטיס. בדוק שלבוט יש הרשאה ליצור שרשורים פרטיים.", lang),
                                                   ephemeral=True)
        self.bot.store.open_ticket(gid, str(thread.id), str(member.id), str(member), "")
        await interaction.followup.send(tr("🎫 הכרטיס שלך נפתח: {link}", lang, link=thread.mention), ephemeral=True)
        await self._log(guild, cfg, tr("🎫 כרטיס #{n} נפתח על ידי {user}: {link}", lang, n=number, user=member.mention,
                                       link=thread.mention))

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        guild, member = interaction.guild, interaction.user
        cfg = self.bot.cfg(guild, "ticketing")
        if not cfg:
            return await reply_off(interaction, self.bot)
        lang, thread = cfg["_lang"], interaction.channel
        ticket = self.bot.store.ticket_by_thread(str(getattr(thread, "id", "")))
        if ticket is None or not isinstance(thread, discord.Thread):
            return await interaction.response.send_message(tr("זה לא כרטיס תמיכה.", lang), ephemeral=True)
        staff = has_any_role(member, cfg["support_role_ids"]) or member.guild_permissions.manage_threads
        if str(member.id) != ticket["user_id"] and not staff:
            return await interaction.response.send_message(tr("רק מי שפתח את הכרטיס או הצוות יכולים לסגור אותו.", lang),
                                                           ephemeral=True)
        await interaction.response.send_message(tr("🔒 הכרטיס נסגר על ידי {user}.", lang, user=member.mention),
                                                allowed_mentions=discord.AllowedMentions.none())
        self.bot.store.close_ticket(str(thread.id))
        try:
            await thread.edit(archived=True, locked=True, reason=f"closed by {member}")
        except discord.HTTPException:
            pass
        await self._log(guild, cfg, tr("🔒 כרטיס #{n} נסגר על ידי {user}", lang, n=ticket["number"], user=member.mention))

    async def _log(self, guild: discord.Guild, cfg: dict, text: str) -> None:
        channel = text_channel(guild, cfg["log_channel_id"])
        if channel is not None:
            try:
                await channel.send(embed=discord.Embed(description=text, color=themed(cfg["_lang"], parse_color("#6D5DFC"))),
                                   allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    @app_commands.command(name="ticket-panel", description=N_("פרסום הכפתור לפתיחת כרטיסים בערוץ שנבחר באפליקציה"))
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def ticket_panel(self, interaction: discord.Interaction):
        try:
            msg = await self.publish_panel(interaction.guild)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        except discord.HTTPException as exc:
            return await interaction.response.send_message(str(exc.text or exc.status), ephemeral=True)
        await interaction.response.send_message(tr("✅ פורסם: {link}", self.bot.lang(interaction.guild), link=msg.jump_url),
                                                ephemeral=True)

    @app_commands.command(name="ticket-close", description=N_("סגירת הכרטיס הנוכחי"))
    @app_commands.guild_only()
    async def ticket_close(self, interaction: discord.Interaction):
        await self.close_ticket(interaction)
