"""Support tickets: a panel with a button opens a private thread between the member and the staff roles."""

from ..i18n import N_, tr
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .common import COLOR_INFO, has_any_role, reply_off, text_channel, themed
from .welcome import parse_color

log = logging.getLogger("botd.tickets")


STYLES = {"blue": discord.ButtonStyle.primary, "grey": discord.ButtonStyle.secondary,
          "green": discord.ButtonStyle.success, "red": discord.ButtonStyle.danger}


def button_style(color: str, default: str = "blue") -> discord.ButtonStyle:
    return STYLES.get(color or default, STYLES[default])


def safe_emoji(text: str):
    """An emoji for a button, or None when the text is not one Discord accepts there."""
    t = (text or "").strip()
    if not t:
        return None
    try:
        pe = discord.PartialEmoji.from_str(t)
    except Exception:  # noqa: BLE001
        return None
    if pe.id is None and (len(t) > 8 or t.isascii()):  # words are not emojis
        return None
    return pe


def panel_view(cfg: dict) -> discord.ui.View:
    """The panel's buttons: one per ticket type, or the single default button."""
    view = discord.ui.View(timeout=None)
    types = [t for t in cfg.get("types") or [] if t.get("label") or t.get("emoji")]
    if not types:
        view.add_item(discord.ui.Button(label=(cfg.get("button_label") or "")[:80] or None, custom_id="ticket:open",
                                        emoji=safe_emoji(cfg.get("button_emoji")) or (None if cfg.get("button_label") else "🎫"),
                                        style=button_style(cfg.get("button_color"))))
        return view
    for i, t in enumerate(types[:20]):
        view.add_item(discord.ui.Button(label=(t.get("label") or "")[:80] or None, custom_id=f"ticket:open:{i}",
                                        emoji=safe_emoji(t.get("emoji")) or (None if t.get("label") else "🎫"),
                                        style=button_style(t.get("color"))))
    return view


def close_view(cfg: dict, lang) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=tr("סגור כרטיס", lang), custom_id="ticket:close",
                                    emoji=safe_emoji(cfg.get("close_emoji")) or "🔒", style=button_style(cfg.get("close_color"), "red")))
    return view


class Ticketing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Ticket buttons keep working after restarts: they are recognised by their id, not by a saved view."""
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if cid == "ticket:close":
            await self.close_ticket(interaction)
        elif cid == "ticket:open" or cid.startswith("ticket:open:"):
            index = int(cid.rsplit(":", 1)[1]) if cid.count(":") == 2 and cid.rsplit(":", 1)[1].isdigit() else None
            await self.open_ticket(interaction, index)

    async def publish_panel(self, guild: discord.Guild) -> discord.Message:
        cfg = self.bot.cfg(guild, "ticketing")
        if not cfg:
            raise ValueError(tr("המודול כבוי בשרת הזה"))
        channel = text_channel(guild, cfg["panel_channel_id"])
        if channel is None:
            raise ValueError(tr("בחר ערוץ לכפתור ושמור"))
        embed = discord.Embed(title=cfg["panel_title"][:256] or None, description=cfg["panel_message"][:4000] or None,
                              color=themed(cfg["_lang"], COLOR_INFO))
        return await channel.send(embed=embed, view=panel_view(cfg))

    async def open_ticket(self, interaction: discord.Interaction, type_index: int | None = None) -> None:
        guild, member = interaction.guild, interaction.user
        cfg = self.bot.cfg(guild, "ticketing")
        if not cfg:
            return await reply_off(interaction, self.bot)
        lang = cfg["_lang"]
        types = [t for t in cfg.get("types") or [] if t.get("label") or t.get("emoji")]
        kind = types[type_index] if type_index is not None and type_index < len(types) else None
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
            icon = (kind or {}).get("emoji") if kind and not str(kind.get("emoji") or "").startswith("<") else None
            title = f"{icon or '🎫'} {number:04d} · " + (f"{kind['label']} · " if kind and kind.get("label") else "") + member.display_name
            thread = await channel.create_thread(name=title[:100],
                                                 type=discord.ChannelType.private_thread, invitable=False,
                                                 reason=f"ticket #{number}")
            await thread.add_user(member)
            role_ids = list(dict.fromkeys(([kind["role_id"]] if kind and kind.get("role_id") else []) + list(cfg["support_role_ids"])))
            roles = " ".join(f"<@&{r}>" for r in role_ids)
            welcome = ((kind or {}).get("welcome") or cfg["welcome_message"]).replace("{user}", member.mention) \
                .replace("{number}", str(number)).replace("{type}", (kind or {}).get("label") or "")
            await thread.send(f"{welcome}\n{roles}".strip()[:2000], view=close_view(cfg, lang),
                              allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False))
        except discord.HTTPException as exc:
            log.warning(tr("פתיחת כרטיס נכשלה בשרת %s: %s"), guild.name, exc.text or exc.status)
            return await interaction.followup.send(tr("לא הצלחתי לפתוח כרטיס. בדוק שלבוט יש הרשאה ליצור שרשורים פרטיים.", lang),
                                                   ephemeral=True)
        self.bot.store.open_ticket(gid, str(thread.id), str(member.id), str(member), (kind or {}).get("label") or "")
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
        type_roles = [t["role_id"] for t in cfg.get("types") or [] if t.get("role_id")]
        staff = has_any_role(member, list(cfg["support_role_ids"]) + type_roles) or member.guild_permissions.manage_threads
        if str(member.id) != ticket["user_id"] and not staff:
            return await interaction.response.send_message(tr("רק מי שפתח את הכרטיס או הצוות יכולים לסגור אותו.", lang),
                                                           ephemeral=True)
        await interaction.response.send_message(tr("🔒 הכרטיס נסגר על ידי {user}.", lang, user=member.mention),
                                                allowed_mentions=discord.AllowedMentions.none())
        self.bot.store.close_ticket(str(thread.id))
        if cfg.get("dm_transcript") or cfg.get("log_transcript"):
            await self._send_transcript(guild, cfg, thread, ticket, member)
        try:
            await thread.edit(archived=True, locked=True, reason=f"closed by {member}")
        except discord.HTTPException:
            pass
        await self._log(guild, cfg, tr("🔒 כרטיס #{n} נסגר על ידי {user}", lang, n=ticket["number"], user=member.mention))

    async def transcript(self, thread: discord.Thread, ticket: dict) -> str:
        """Everything said in the ticket, as plain text (oldest first)."""
        lines = [f"Ticket #{ticket['number']} · {thread.guild.name} · {thread.name}", ""]
        async for m in thread.history(limit=2000, oldest_first=True):
            if m.type not in (discord.MessageType.default, discord.MessageType.reply):
                continue
            body = m.clean_content or ""
            extras = [a.url for a in m.attachments] + [f"[embed] {e.title or e.description or ''}"[:200] for e in m.embeds]
            text = " ".join(x for x in [body, *extras] if x)
            lines.append(f"[{m.created_at:%Y-%m-%d %H:%M} UTC] {m.author.display_name}: {text}")
        return "\n".join(lines)

    async def _send_transcript(self, guild, cfg, thread, ticket, closer) -> None:
        import io

        lang = cfg["_lang"]
        try:
            data = (await self.transcript(thread, ticket)).encode("utf-8")
        except discord.HTTPException as exc:
            log.warning(tr("יצירת תיעוד הכרטיס נכשלה: %s"), exc.text or exc.status)
            return
        name = f"ticket-{ticket['number']:04d}.txt"
        text = tr("📄 הכרטיס שלך #{n} בשרת **{server}** נסגר. מצורף תיעוד השיחה.", lang, n=ticket["number"],
                  server=discord.utils.escape_markdown(guild.name))
        if cfg.get("dm_transcript"):
            opener = guild.get_member(int(ticket["user_id"])) or self.bot.get_user(int(ticket["user_id"]))
            try:
                if opener is None:
                    opener = await self.bot.fetch_user(int(ticket["user_id"]))
                await opener.send(text, file=discord.File(io.BytesIO(data), name))
            except discord.HTTPException:  # DMs closed
                try:
                    await thread.send(tr("לא הצלחתי לשלוח את התיעוד בהודעה פרטית (ההודעות הפרטיות סגורות).", lang))
                except discord.HTTPException:
                    pass
        if cfg.get("log_transcript"):
            channel = text_channel(guild, cfg["log_channel_id"])
            if channel is not None:
                try:
                    await channel.send(tr("📄 תיעוד כרטיס #{n}", lang, n=ticket["number"]), file=discord.File(io.BytesIO(data), name))
                except discord.HTTPException:
                    pass

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
