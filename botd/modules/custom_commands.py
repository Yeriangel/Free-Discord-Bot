"""Custom commands (like MEE6): text commands (!rules) and real slash commands (/type), set per server in the app.

A response can use variables:
  {args} everything written after the command · {arg1}..{arg9} single words
  {random} a random number between the first two numbers in the args (e.g. "/randomnumber 1 to 1000"), else 1-100
  {random:1-6} a random number in a fixed range · {choose:a|b|c} one of the options
  {user} {username} {server} {member_count} {channel}
A command can also delete the message that called it, answer as a reply / in the channel / in a DM, and give,
take or toggle a role. Slash commands are registered for each server separately, so every server has its own.
"""

from ..i18n import tr
import hashlib
import json
import logging
import random
import re

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import USER_PINGS
from .common import can_manage_role, fill

log = logging.getLogger("botd.custom_commands")
_rng = random.SystemRandom()
ARGS_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False)


def match_command(content: str, prefix: str, commands_list: list[dict]) -> dict | None:
    if not prefix or not content.startswith(prefix):
        return None
    rest = content[len(prefix):].strip()
    if not rest:
        return None
    name = rest.split(maxsplit=1)[0].lower()
    return next((c for c in commands_list if c["name"] == name and c.get("kind", "prefix") in ("prefix", "both")), None)


def command_args(content: str, prefix: str) -> str:
    rest = content[len(prefix):].strip()
    parts = rest.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


def _random_from(args: str) -> str:
    nums = [int(n) for n in re.findall(r"-?\d+", args)[:2]]
    lo, hi = (nums[0], nums[1]) if len(nums) == 2 else (1, nums[0]) if len(nums) == 1 else (1, 100)
    if lo > hi:
        lo, hi = hi, lo
    hi = min(hi, lo + 10**12)
    return str(_rng.randint(lo, hi))


def render(template: str, args: str = "", *, member=None, guild=None, channel=None, lang: str = "he") -> str:
    """Fills a custom command response. Safe: no str.format, variables cannot reach Python objects."""
    words = args.split()
    text = template.replace("{args}", args)
    text = re.sub(r"\{arg([1-9])\}", lambda m: words[int(m.group(1)) - 1] if len(words) >= int(m.group(1)) else "", text)

    def rand(m: re.Match) -> str:
        if m.group(1):
            lo, hi = int(m.group(1)), int(m.group(2))
            return str(_rng.randint(min(lo, hi), max(lo, hi)))
        return _random_from(args)
    text = re.sub(r"\{random(?::(-?\d+)\s*-\s*(-?\d+))?\}", rand, text)
    text = re.sub(r"\{choose:([^{}]+)\}", lambda m: _rng.choice([o.strip() for o in m.group(1).split("|") if o.strip()] or [""]), text)
    return fill(text, member=member, guild=guild, channel=channel, lang=lang)[:2000]


class CustomCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---- text commands (!name) ----------------------------------------------------------------
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
        lang = self.bot.lang(message.guild)
        note = await self._apply_role(message.guild, message.author, cmd)
        text = render(cmd["response"], command_args(message.content, cfg["prefix"]), member=message.author,
                      guild=message.guild, channel=message.channel, lang=lang)
        text = "\n".join(t for t in (text, note) if t.strip())
        if cmd.get("delete_trigger"):
            try:
                await message.delete()
            except discord.HTTPException:
                pass  # no 'Manage Messages' permission: just answer
        if not text:
            return
        try:
            mode = cmd.get("send_mode", "channel")
            if mode == "dm":
                await message.author.send(text)
            elif mode == "reply" and not cmd.get("delete_trigger"):
                await message.reply(text, mention_author=False, allowed_mentions=ARGS_MENTIONS)
            else:
                await message.channel.send(text, allowed_mentions=ARGS_MENTIONS)
        except discord.HTTPException as exc:
            log.warning(tr("פקודה מותאמת נכשלה: %s"), exc.text or exc.status)

    # ---- slash commands (/name), registered per server -----------------------------------------
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            if self.bot.manager.owns(str(guild.id)):
                await self.sync_guild(guild)

    def _slash_specs(self, guild: discord.Guild) -> list[dict]:
        cfg = self.bot.cfg(guild, "custom_commands")
        if not cfg:
            return []
        return [c for c in cfg["commands"] if c.get("kind") in ("slash", "both")][:50]

    async def sync_guild(self, guild: discord.Guild) -> int:
        """Registers this server's slash custom commands with Discord (only when they changed)."""
        specs = self._slash_specs(guild)
        digest = hashlib.sha256(json.dumps([[c["name"], c.get("description", ""), c.get("arg_hint", "")] for c in specs],
                                           sort_keys=True).encode()).hexdigest()
        key = f"cc_hash:{self.bot.application_id}:{guild.id}"
        self.bot.tree.clear_commands(guild=guild)
        for spec in specs:
            self.bot.tree.add_command(self._make_command(spec, guild), guild=guild)
        if self.bot.store.get_kv(key) == digest:
            return len(specs)
        try:
            await self.bot.tree.sync(guild=guild)
            self.bot.store.set_kv(key, digest)
            log.info(tr("נרשמו %d פקודות מותאמות בשרת %s"), len(specs), guild.name)
        except discord.HTTPException as exc:
            log.warning(tr("רישום פקודות מותאמות נכשל בשרת %s: %s"), guild.name, exc.text or exc.status)
        return len(specs)

    def _make_command(self, spec: dict, guild: discord.Guild) -> app_commands.Command:
        name = spec["name"]

        async def callback(interaction: discord.Interaction, text: str | None = None):
            await self.run_slash(interaction, name, text or "")

        callback = app_commands.describe(text=(spec.get("arg_hint") or tr("טקסט", self.bot.lang(guild)))[:100])(callback)
        desc = (spec.get("description") or tr("פקודה מותאמת", self.bot.lang(guild)))[:100]
        return app_commands.Command(name=name, description=desc, callback=callback)

    async def run_slash(self, interaction: discord.Interaction, name: str, args: str) -> None:
        cfg = self.bot.cfg(interaction.guild, "custom_commands")
        cmd = next((c for c in (cfg or {}).get("commands", []) if c["name"] == name), None)
        lang = self.bot.lang(interaction.guild)
        if not cfg or cmd is None:
            return await interaction.response.send_message(tr("הפקודה הזו כבויה בשרת הזה.", lang), ephemeral=True)
        note = await self._apply_role(interaction.guild, interaction.user, cmd)
        text = render(cmd["response"], args, member=interaction.user, guild=interaction.guild, channel=interaction.channel,
                      lang=lang)
        text = "\n".join(t for t in (text, note) if t.strip()) or "✅"
        mode = cmd.get("send_mode", "channel")
        try:
            if mode == "reply" and not cmd.get("delete_trigger"):
                await interaction.response.send_message(text, allowed_mentions=ARGS_MENTIONS)
            elif mode == "dm":
                await interaction.user.send(text)
                await interaction.response.send_message(tr("📬 נשלח לך בהודעה פרטית.", lang), ephemeral=True)
            else:  # in the channel as the bot's own message: nobody sees who used the command
                await interaction.response.send_message("✅", ephemeral=True)
                await interaction.channel.send(text, allowed_mentions=ARGS_MENTIONS)
        except discord.HTTPException as exc:
            log.warning(tr("פקודה מותאמת נכשלה: %s"), exc.text or exc.status)

    async def _apply_role(self, guild: discord.Guild, member, cmd: dict) -> str:
        action = cmd.get("role_action", "none")
        if action == "none" or not cmd.get("role_id") or not isinstance(member, discord.Member):
            return ""
        lang = self.bot.lang(guild)
        role = guild.get_role(int(cmd["role_id"]))
        if not can_manage_role(guild, role):
            log.warning(tr("פקודה מותאמת '%s': אין לבוט אפשרות לנהל את התפקיד"), cmd["name"])
            return ""
        has = role in member.roles
        add = action == "add" or (action == "toggle" and not has)
        if (add and has) or (not add and not has):
            return ""
        try:
            if add:
                await member.add_roles(role, reason=tr("פקודה מותאמת !{name}", lang, name=cmd['name']))
            else:
                await member.remove_roles(role, reason=tr("פקודה מותאמת !{name}", lang, name=cmd['name']))
        except discord.HTTPException as exc:
            log.warning(tr("שינוי תפקיד בפקודה מותאמת נכשל: %s"), exc.text or exc.status)
            return ""
        return tr("✅ קיבלת את התפקיד **{role}**", lang, role=role.name) if add else tr("✅ התפקיד **{role}** הוסר", lang, role=role.name)
