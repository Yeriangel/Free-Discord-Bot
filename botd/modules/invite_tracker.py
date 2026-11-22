"""Invite tracker: works out who invited each new member by comparing invite use counts."""

from ..i18n import N_, tr
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import USER_PINGS
from .common import reply_off, text_channel

log = logging.getLogger("botd.invites")


def find_used_invite(before: dict[str, int], invites: list) -> object | None:
    """The invite whose use count went up since the last snapshot (None if unclear)."""
    used = [inv for inv in invites if inv.uses > before.get(inv.code, 0)]
    return used[0] if len(used) == 1 else None


class InviteTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache: dict[int, dict[str, int]] = {}

    async def _snapshot(self, guild: discord.Guild) -> list:
        if not guild.me or not guild.me.guild_permissions.manage_guild:
            return []
        try:
            invites = await guild.invites()
        except discord.HTTPException:
            return []
        self.cache[guild.id] = {inv.code: inv.uses or 0 for inv in invites}
        return invites

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            if self.bot.cfg(guild, "invite_tracker"):
                await self._snapshot(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            self.cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            self.cache.get(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = self.bot.cfg(member.guild, "invite_tracker")
        if not cfg:
            return
        before = self.cache.get(member.guild.id)
        invites = await self._snapshot(member.guild)
        used = find_used_invite(before or {}, invites) if before is not None else None
        channel = text_channel(member.guild, cfg["channel_id"])
        gid = str(member.guild.id)
        if used is not None and used.inviter is not None and not member.bot:
            total = self.bot.store.record_invite_join(gid, str(member.id), str(used.inviter.id), used.inviter.display_name)
            text = (cfg["join_message"].replace("{user}", member.mention)
                    .replace("{username}", discord.utils.escape_markdown(member.display_name))
                    .replace("{inviter}", used.inviter.mention).replace("{invites}", str(total))
                    .replace("{code}", used.code))
        else:
            text = (cfg["unknown_message"].replace("{user}", member.mention)
                    .replace("{username}", discord.utils.escape_markdown(member.display_name)))
        if channel is not None and text.strip():
            try:
                await channel.send(text[:2000], allowed_mentions=USER_PINGS)
            except discord.HTTPException as exc:
                log.warning(tr("הודעת מעקב הזמנות נכשלה בשרת %s: %s"), member.guild.name, exc.text or exc.status)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if self.bot.cfg(member.guild, "invite_tracker"):
            self.bot.store.record_invite_leave(str(member.guild.id), str(member.id))

    @app_commands.command(name="invites", description=N_("כמה אנשים הזמנת (או חבר אחר)"))
    @app_commands.guild_only()
    async def invites(self, interaction: discord.Interaction, member: discord.Member | None = None):
        cfg = self.bot.cfg(interaction.guild, "invite_tracker")
        if not cfg:
            return await reply_off(interaction, self.bot)
        who = member or interaction.user
        st = self.bot.store.invite_stats(str(interaction.guild_id), str(who.id))
        await interaction.response.send_message(
            tr("📨 {user} הזמין **{total}** אנשים ({joins} הצטרפו, {leaves} עזבו).", cfg["_lang"],
               user=who.mention, total=st["total"], joins=st["joins"], leaves=st["leaves"]),
            allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="invites-top", description=N_("מי הזמין הכי הרבה אנשים"))
    @app_commands.guild_only()
    async def invites_top(self, interaction: discord.Interaction):
        cfg = self.bot.cfg(interaction.guild, "invite_tracker")
        if not cfg:
            return await reply_off(interaction, self.bot)
        rows = self.bot.store.invite_board(str(interaction.guild_id), 10)
        if not rows:
            return await interaction.response.send_message(tr("עוד אין הזמנות במעקב.", cfg["_lang"]), ephemeral=True)
        lines = [f"**{i}.** <@{r['user_id']}> · {r['total']}" for i, r in enumerate(rows, 1)]
        embed = discord.Embed(title=tr("🏆 המזמינים המובילים", cfg["_lang"]), description="\n".join(lines), color=0x6D5DFC)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())
