"""Small helpers shared by the module cogs."""

import discord

from ..i18n import tr

COLOR_INFO = 0x5865F2
COLOR_OK = 0x2FBF71
COLOR_WARN = 0xF0B232
COLOR_DANGER = 0xEF4B5F


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
    embed = discord.Embed(title=action, color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name=tr("משתמש", lang), value=user_line(target), inline=False)
    if moderator is not None:
        embed.add_field(name=tr("בוצע על ידי", lang), value=user_line(moderator), inline=False)
    if reason:
        embed.add_field(name=tr("סיבה", lang), value=reason[:1000], inline=False)
    if extra:
        embed.add_field(name=tr("פרטים", lang), value=extra[:1000], inline=False)
    return embed
