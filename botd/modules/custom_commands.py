"""Custom text commands (e.g. !rules). Needs the Message Content intent."""

from ..i18n import tr
import logging

import discord
from discord.ext import commands

from ..bot import USER_PINGS
from .common import can_manage_role, fill

log = logging.getLogger("botd.custom_commands")


def match_command(content: str, prefix: str, commands_list: list[dict]) -> dict | None:
    if not prefix or not content.startswith(prefix):
        return None
    rest = content[len(prefix):].strip()
    if not rest:
        return None
    name = rest.split(maxsplit=1)[0].lower()
    return next((c for c in commands_list if c["name"] == name), None)


class CustomCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        cfg = self.bot.cfg(message.guild, "custom_commands")
        if not cfg:
            return
        cmd = match_command(message.content, cfg["prefix"], cfg["commands"])
        if cmd is None:
            return
        note = await self._apply_role(message, cmd)
        text = fill(cmd["response"], member=message.author, guild=message.guild, channel=message.channel,
                    lang=self.bot.lang(message.guild))
        text = "\n".join(t for t in (text, note) if t.strip())
        if not text:
            return
        try:
            await message.channel.send(text, allowed_mentions=USER_PINGS)
        except discord.HTTPException as exc:
            log.warning(tr("פקודה מותאמת נכשלה: %s"), exc.text or exc.status)

    async def _apply_role(self, message: discord.Message, cmd: dict) -> str:
        action = cmd.get("role_action", "none")
        if action == "none" or not cmd.get("role_id") or not isinstance(message.author, discord.Member):
            return ""
        lang = self.bot.lang(message.guild)
        role = message.guild.get_role(int(cmd["role_id"]))
        if not can_manage_role(message.guild, role):
            log.warning(tr("פקודה מותאמת '%s': אין לבוט אפשרות לנהל את התפקיד"), cmd["name"])
            return ""
        has = role in message.author.roles
        add = action == "add" or (action == "toggle" and not has)
        if (add and has) or (not add and not has):
            return ""
        try:
            if add:
                await message.author.add_roles(role, reason=tr("פקודה מותאמת !{name}", lang, name=cmd['name']))
            else:
                await message.author.remove_roles(role, reason=tr("פקודה מותאמת !{name}", lang, name=cmd['name']))
        except discord.HTTPException as exc:
            log.warning(tr("שינוי תפקיד בפקודה מותאמת נכשל: %s"), exc.text or exc.status)
            return ""
        return tr("✅ קיבלת את התפקיד **{role}**", lang, role=role.name) if add else tr("✅ התפקיד **{role}** הוסר", lang, role=role.name)
