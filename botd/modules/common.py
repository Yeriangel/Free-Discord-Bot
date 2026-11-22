"""Small helpers shared by the module cogs."""

import discord

from ..i18n import tr

COLOR_INFO = 0x5865F2
COLOR_OK = 0x2FBF71
COLOR_WARN = 0xF0B232
COLOR_DANGER = 0xEF4B5F

# Every embed color the bot uses, by name. Servers can change each one (app: Customize -> Colors & emojis).
PALETTE = {"info": COLOR_INFO, "success": COLOR_OK, "warning": COLOR_WARN, "danger": COLOR_DANGER,
           "brand": 0x6D5DFC, "starboard": 0xF5C542, "birthdays": 0xF47FFF, "done": 0x3BA55C}
_PALETTE_BY_VALUE = {v: k for k, v in PALETTE.items()}
EMOJIS = {"vote_up": "👍", "vote_down": "👎", "giveaway": "🎉", "ticket": "🎫", "close": "🔒"}


def themed(lang, value: int) -> int:
    """The server's own color for one of the palette colors (unchanged if the server did not set one)."""
    name = _PALETTE_BY_VALUE.get(value)
    custom = (getattr(lang, "colors", None) or {}).get(name) if name else None
    if custom:
        try:
            return int(str(custom).lstrip("#"), 16)
        except ValueError:
            pass
    return value


def emoji(lang, name: str) -> str:
    return (getattr(lang, "emojis", None) or {}).get(name) or EMOJIS[name]


def human_duration(seconds: float, lang: str = "he") -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days >= 365:
        return tr("{n} שנים", lang, n=days // 365) if days >= 730 else tr("שנה", lang)
    if days >= 2:
        return tr("{n} ימים", lang, n=days)
    if days == 1:
        return tr("יום", lang)
    if hours >= 2:
        return tr("{n} שעות", lang, n=hours)
    if hours == 1:
        return tr("שעה", lang)
    return tr("{n} דקות", lang, n=minutes) if minutes != 1 else tr("דקה", lang)


def fill(template: str, *, member: discord.abc.User | None = None, guild: discord.Guild | None = None,
         channel: discord.abc.GuildChannel | None = None, lang: str = "he") -> str:
    """Safe placeholder replacement (no str.format, so templates cannot reach object attributes)."""
    values = {}
    if member is not None:
        values["{user}"] = member.mention
        values["{username}"] = discord.utils.escape_markdown(member.display_name)
        joined = getattr(member, "joined_at", None)
        values["{duration}"] = (human_duration((discord.utils.utcnow() - joined).total_seconds(), lang) if joined
                                else tr("זמן לא ידוע", lang))
    if guild is not None:
        values["{server}"] = discord.utils.escape_markdown(guild.name)
        values["{member_count}"] = str(guild.member_count or "")
    if channel is not None:
        values["{channel}"] = getattr(channel, "mention", "")
    for key, value in values.items():
        template = template.replace(key, value)
    return template[:2000]


def text_channel(guild: discord.Guild, channel_id: str):
    if not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    return channel if channel is not None and hasattr(channel, "send") else None


def can_manage_role(guild: discord.Guild, role: discord.Role | None) -> bool:
    me = guild.me
    return (
        role is not None
        and me is not None
        and me.guild_permissions.manage_roles
        and not role.managed
        and not role.is_default()
        and role < me.top_role
    )


def user_line(user: discord.abc.User) -> str:
    return f"{user.mention} · `{user}` · `{user.id}`"


def mod_embed(action: str, target: discord.abc.User, moderator: discord.abc.User | None,
              reason: str | None, color: int = COLOR_DANGER, extra: str = "", lang: str = "he") -> discord.Embed:
    """`action` is already translated by the caller."""
    embed = discord.Embed(title=action, color=themed(lang, color), timestamp=discord.utils.utcnow())
    embed.add_field(name=tr("משתמש", lang), value=user_line(target), inline=False)
    if moderator is not None:
        embed.add_field(name=tr("בוצע על ידי", lang), value=user_line(moderator), inline=False)
    if reason:
        embed.add_field(name=tr("סיבה", lang), value=reason[:1000], inline=False)
    if extra:
        embed.add_field(name=tr("פרטים", lang), value=extra[:1000], inline=False)
    return embed


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> int | None:
    """'10m', '2h', '1d12h', '1w', '90' (minutes) -> seconds. None if it cannot be read."""
    import re

    text = (text or "").strip().lower().replace(" ", "")
    if not text:
        return None
    if text.isdigit():
        return int(text) * 60
    parts = re.findall(r"(\d+)([smhdw])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    return sum(int(n) * _DURATION_UNITS[u] for n, u in parts)


def has_any_role(member, role_ids) -> bool:
    ids = set(role_ids or [])
    return bool(ids) and any(str(r.id) in ids for r in getattr(member, "roles", []))


async def reply_off(interaction: discord.Interaction, bot) -> None:
    await interaction.response.send_message(
        tr("המודול הזה כבוי בשרת הזה. אפשר להפעיל אותו באפליקציה.", bot.lang(interaction.guild)), ephemeral=True)


def relative_time(ts: float) -> str:
    """Discord renders this as 'in 5 minutes' / '2 hours ago' in each user's own language."""
    return f"<t:{int(ts)}:R>"
