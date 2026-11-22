"""Give roles to new members (after membership screening, if the server uses it)."""

from ..i18n import tr
import logging

import discord
from discord.ext import commands

from .common import can_manage_role

log = logging.getLogger("botd.autorole")


class AutoRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _give(self, member: discord.Member) -> None:
        cfg = self.bot.cfg(member.guild, "autorole")
        if not cfg or not cfg["role_ids"] or (member.bot and not cfg["include_bots"]):
            return
        roles, skipped = [], []
        for rid in cfg["role_ids"]:
            role = member.guild.get_role(int(rid))
            (roles if can_manage_role(member.guild, role) else skipped).append(role or rid)
        if skipped:
            log.warning(tr("תפקיד אוטומטי: %d תפקידים מעל תפקיד הבוט או חסרים בשרת %s"), len(skipped), member.guild.name)
        if roles:
            try:
                await member.add_roles(*roles, reason=tr("תפקיד אוטומטי", self.bot.lang(member.guild)))
            except discord.HTTPException as exc:
                log.warning(tr("תפקיד אוטומטי נכשל בשרת %s: %s"), member.guild.name, exc.text or exc.status)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not member.pending:
            await self._give(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.pending and not after.pending:
            await self._give(after)
