"""Reaction roles: the bot posts a message with reactions; reacting gives/removes a role."""

from ..i18n import tr
import logging

import discord
from discord.ext import commands

from .. import catalog
from .common import can_manage_role, text_channel

log = logging.getLogger("botd.reaction_roles")

MAX_PAIRS = 20


def emoji_key(emoji) -> str:
    pe = emoji if isinstance(emoji, discord.PartialEmoji) else discord.PartialEmoji.from_str(str(emoji).strip())
    return str(pe.id) if pe.id else (pe.name or "")


class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _groups(self, gid: str) -> list[dict]:
        return catalog.merged_config("reaction_roles", self.bot.store.raw_config(gid, "reaction_roles"))["groups"]

    def _save(self, gid: str, groups: list[dict]) -> None:
        self.bot.store.set_config(gid, "reaction_roles", {"groups": groups})

    def _role_for(self, payload: discord.RawReactionActionEvent) -> tuple[discord.Guild, discord.Role] | None:
        if payload.guild_id is None or (self.bot.user and payload.user_id == self.bot.user.id):
            return None
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or not self.bot.cfg(guild, "reaction_roles"):
            return None
        key = emoji_key(payload.emoji)
        for group in self._groups(str(guild.id)):
            if group.get("message_id") != str(payload.message_id):
                continue
            for pair in group.get("pairs", []):
                if emoji_key(pair["emoji"]) == key:
                    role = guild.get_role(int(pair["role_id"]))
                    if can_manage_role(guild, role):
                        return guild, role
                    log.warning(tr("תפקיד לפי ריאקשן: אין לבוט אפשרות לתת את התפקיד בשרת %s"), guild.name)
        return None

    async def _member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        return None if member.bot else member

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        found = self._role_for(payload)
        if not found:
            return
        guild, role = found
        member = payload.member if payload.member and not payload.member.bot else await self._member(guild, payload.user_id)
        if member is not None and role not in member.roles:
            try:
                await member.add_roles(role, reason=tr("תפקיד לפי ריאקשן", self.bot.lang(guild)))
            except discord.HTTPException as exc:
                log.warning(tr("הוספת תפקיד נכשלה: %s"), exc.text or exc.status)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        found = self._role_for(payload)
        if not found:
            return
        guild, role = found
        member = await self._member(guild, payload.user_id)
        if member is not None and role in member.roles:
            try:
                await member.remove_roles(role, reason=tr("תפקיד לפי ריאקשן", self.bot.lang(guild)))
            except discord.HTTPException as exc:
                log.warning(tr("הסרת תפקיד נכשלה: %s"), exc.text or exc.status)

    # ---- buttons (mode "buttons"): clicking toggles the role -----------------------------------------------------
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("rr:"):
            return
        guild = interaction.guild
        lang = self.bot.lang(guild)
        if not self.bot.cfg(guild, "reaction_roles"):
            return await interaction.response.send_message(tr("המודול הזה כבוי בשרת הזה. אפשר להפעיל אותו באפליקציה.", lang),
                                                           ephemeral=True)
        role = guild.get_role(int(custom_id[3:])) if custom_id[3:].isdigit() else None
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if role is None or member is None:
            return await interaction.response.send_message(tr("התפקיד הזה כבר לא קיים.", lang), ephemeral=True)
        if not can_manage_role(guild, role):
            return await interaction.response.send_message(tr("אין לבוט אפשרות לתת את התפקיד הזה.", lang), ephemeral=True)
        try:
            if role in member.roles:
                await member.remove_roles(role, reason=tr("תפקיד לפי ריאקשן", lang))
                msg = tr("התפקיד {role} הוסר ממך.", lang, role=role.mention)
            else:
                await member.add_roles(role, reason=tr("תפקיד לפי ריאקשן", lang))
                msg = tr("קיבלת את התפקיד {role}.", lang, role=role.mention)
        except discord.HTTPException as exc:
            log.warning(tr("הוספת תפקיד נכשלה: %s"), exc.text or exc.status)
            msg = tr("דיסקורד לא איפשר לשנות את התפקיד.", lang)
        await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    # ---- called from the local API -----------------------------------------
    def _resolve_emoji(self, guild: discord.Guild, text: str) -> str:
        """':name:' or 'name' of a server emoji -> <:name:id>; anything else stays as it is (normal emojis)."""
        t = (text or "").strip()
        if not t or t.startswith("<"):
            return t
        name = t.strip(":")
        if t.startswith(":") or (name and name.replace("_", "").isalnum() and not name.isdigit()):
            found = discord.utils.get(guild.emojis, name=name)
            if found:
                return str(found)
        return t

    def _clean(self, guild: discord.Guild, data: dict) -> dict:
        """Checks what the app sent and returns the group to save (without channel / message)."""
        pairs = data.get("pairs") or []
        if not 1 <= len(pairs) <= MAX_PAIRS:
            raise ValueError(tr("צריך בין 1 ל-{n} זוגות של אימוג'י ותפקיד", n=MAX_PAIRS))
        mode = "buttons" if data.get("mode") == "buttons" else "reactions"
        clean, seen = [], set()
        for pair in pairs:
            emoji = self._resolve_emoji(guild, str(pair.get("emoji", "")))
            label = str(pair.get("label") or "").strip()[:80]
            role = guild.get_role(int(pair["role_id"])) if str(pair.get("role_id", "")).isdigit() else None
            if role is None or (not emoji and not (mode == "buttons" and label)):
                raise ValueError(tr("לכל שורה צריך אימוג'י ותפקיד"))
            if not can_manage_role(guild, role):
                raise ValueError(tr("התפקיד @{role} מעל תפקיד הבוט, או שלבוט אין 'ניהול תפקידים'", role=role.name))
            key = emoji_key(emoji) if emoji else "label:" + label
            if key in seen:
                raise ValueError(tr("האימוג'י {emoji} מופיע פעמיים", emoji=emoji or label))
            seen.add(key)
            clean.append({"emoji": emoji, "label": label, "role_id": str(role.id), "role_name": role.name})
        color = str(data.get("color") or "").strip()
        return {"style": "embed" if data.get("style") == "embed" else "text", "mode": mode,
                "title": str(data.get("title") or "").strip()[:256], "content": str(data.get("content") or "").strip(),
                "color": color if len(color) == 7 and color.startswith("#") else "", "pairs": clean}

    def _render(self, guild: discord.Guild, group: dict):
        """-> (text, embed, view) of the message for a group."""
        from .common import themed, COLOR_INFO
        from .embeds import parse_color

        lang = self.bot.lang(guild)
        lines = "\n".join(f"{p['emoji']}  <@&{p['role_id']}>" + (f" · {p['label']}" if p.get("label") else "")
                          for p in group["pairs"])
        body = group["content"] or (tr("**בחרו תפקיד:**", lang) + "\n" + lines if group["style"] == "text" else lines)
        text, embed = None, None
        if group["style"] == "embed":
            color = parse_color(group["color"]) if group.get("color") else themed(lang, COLOR_INFO)
            embed = discord.Embed(title=group.get("title") or tr("בחרו תפקיד", lang), description=body[:4096], color=color)
        else:
            text = ((f"**{group['title']}**\n" if group.get("title") else "") + body)
            if len(text) > 2000:
                raise ValueError(tr("ההודעה ארוכה מדי"))
        view = None
        if group["mode"] == "buttons":
            view = discord.ui.View(timeout=None)
            for p in group["pairs"]:
                view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, custom_id=f"rr:{p['role_id']}",
                                                emoji=p["emoji"] or None, label=p.get("label") or None))
        return text, embed, view

    def _check_channel(self, guild: discord.Guild, channel_id: str, mode: str):
        channel = text_channel(guild, channel_id)
        if channel is None:
            raise ValueError(tr("בחר ערוץ טקסט"))
        perms = channel.permissions_for(guild.me)
        needs = perms.send_messages and perms.read_message_history and (mode == "buttons" or perms.add_reactions)
        if not needs:
            raise ValueError(tr("לבוט אין בערוץ הזה הרשאות שליחה, הוספת ריאקשנים וקריאת היסטוריה"))
        return channel

    async def _post(self, channel, guild: discord.Guild, group: dict) -> discord.Message:
        text, embed, view = self._render(guild, group)
        try:
            message = await channel.send(content=text, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            raise ValueError(tr("אחד האימוג'ים לא תקין, או שהבוט לא יכול להשתמש בו") if "emoji" in str(exc.text).lower()
                             else tr("דיסקורד דחה את הבקשה: {message}", message=exc.text or exc.status)) from None
        if group["mode"] == "reactions":
            try:
                for pair in group["pairs"]:
                    await message.add_reaction(pair["emoji"])
            except discord.HTTPException:
                await message.delete()
                raise ValueError(tr("אחד האימוג'ים לא תקין, או שהבוט לא יכול להשתמש בו")) from None
        return message

    async def publish(self, guild: discord.Guild, channel_id: str, content: str = "", pairs: list | None = None,
                      **extra) -> dict:
        data = {"content": content, "pairs": pairs or [], **extra}
        group = self._clean(guild, data)
        channel = self._check_channel(guild, channel_id, group["mode"])
        message = await self._post(channel, guild, group)
        group.update(channel_id=str(channel.id), channel_name=channel.name, message_id=str(message.id))
        self._save(str(guild.id), self._groups(str(guild.id)) + [group])
        log.info(tr("פורסמה הודעת תפקידים ב-#%s בשרת %s"), channel.name, guild.name)
        return group

    async def update(self, guild: discord.Guild, message_id: str, channel_id: str, data: dict) -> dict:
        """Edits a posted group and its message in Discord (posts it again if it was deleted or moved channel)."""
        gid = str(guild.id)
        groups = self._groups(gid)
        old = next((g for g in groups if g.get("message_id") == message_id), None)
        if old is None:
            raise KeyError(message_id)
        group = self._clean(guild, data)
        channel = self._check_channel(guild, channel_id or old["channel_id"], group["mode"])
        message = None
        if str(channel.id) == old.get("channel_id"):
            try:
                message = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                message = None
        if message is not None:
            text, embed, view = self._render(guild, group)
            try:
                await message.edit(content=text, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                raise ValueError(tr("דיסקורד דחה את הבקשה: {message}", message=exc.text or exc.status)) from None
            old_keys = {emoji_key(p["emoji"]) for p in old.get("pairs", []) if p.get("emoji")}
            new_keys = {emoji_key(p["emoji"]) for p in group["pairs"] if p.get("emoji")} if group["mode"] == "reactions" else set()
            for p in old.get("pairs", []):  # reactions that are not used any more
                if p.get("emoji") and emoji_key(p["emoji"]) not in new_keys:
                    try:
                        await message.clear_reaction(p["emoji"])
                    except discord.HTTPException:
                        try:
                            await message.remove_reaction(p["emoji"], guild.me)
                        except discord.HTTPException:
                            pass
            if group["mode"] == "reactions":
                for p in group["pairs"]:
                    if emoji_key(p["emoji"]) not in old_keys or old.get("mode") == "buttons":
                        try:
                            await message.add_reaction(p["emoji"])
                        except discord.HTTPException:
                            raise ValueError(tr("אחד האימוג'ים לא תקין, או שהבוט לא יכול להשתמש בו")) from None
        else:  # deleted in Discord, or moved to another channel: post a new message
            message = await self._post(channel, guild, group)
            if str(channel.id) != old.get("channel_id"):
                old_channel = text_channel(guild, old["channel_id"])
                if old_channel is not None:
                    try:
                        await old_channel.get_partial_message(int(message_id)).delete()
                    except discord.HTTPException:
                        pass
        group.update(channel_id=str(channel.id), channel_name=channel.name, message_id=str(message.id))
        self._save(gid, [group if g is old else g for g in groups])
        log.info(tr("הודעת התפקידים ב-#%s עודכנה"), channel.name)
        return group

    async def remove(self, guild_id: str, message_id: str, delete_message: bool) -> None:
        groups = self._groups(guild_id)
        group = next((g for g in groups if g.get("message_id") == message_id), None)
        if group is None:
            raise KeyError(message_id)
        self._save(guild_id, [g for g in groups if g is not group])
        if delete_message:
            guild = self.bot.get_guild(int(guild_id))
            channel = text_channel(guild, group["channel_id"]) if guild else None
            if channel is not None:
                try:
                    await channel.get_partial_message(int(message_id)).delete()
                except discord.HTTPException as exc:
                    log.warning(tr("מחיקת הודעת התפקידים נכשלה: %s"), exc.text or exc.status)
        log.info(tr("הודעת תפקידים %s הוסרה"), message_id)
