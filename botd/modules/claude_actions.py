"""Server actions Claude may request with tool use ("@bot timeout User12345", "rename all chats to ...").

Claude only *proposes* actions. The bot shows the plan with a confirm button that only the allowed user can press,
checks the user's own Discord permissions and role hierarchy, and only then runs anything.
"""

import logging
import re
from datetime import timedelta

import discord

from ..i18n import N_, tr

log = logging.getLogger("botd.claude_actions")

MAX_DELETE_PER_CHANNEL = 200


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


USER = {"type": "string", "description": "A member: mention like <@123>, user ID, or exact name"}
REASON = {"type": "string", "description": "Short reason, or empty"}
TOOLS = [
    {"name": "timeout_member", "description": "Time out (mute) a server member for some minutes.",
     "input_schema": _obj({"user": USER, "minutes": {"type": "integer", "description": "1 to 40320"}, "reason": REASON},
                          ["user", "minutes", "reason"])},
    {"name": "remove_timeout", "description": "Remove a member's timeout.", "input_schema": _obj({"user": USER}, ["user"])},
    {"name": "kick_member", "description": "Kick a member from the server.",
     "input_schema": _obj({"user": USER, "reason": REASON}, ["user", "reason"])},
    {"name": "ban_member", "description": "Ban a member from the server.",
     "input_schema": _obj({"user": USER, "reason": REASON,
                           "delete_message_days": {"type": "integer", "description": "0 to 7"}},
                          ["user", "reason", "delete_message_days"])},
    {"name": "add_role", "description": "Give a role to a member.",
     "input_schema": _obj({"user": USER, "role_id": {"type": "string"}}, ["user", "role_id"])},
    {"name": "remove_role", "description": "Take a role from a member.",
     "input_schema": _obj({"user": USER, "role_id": {"type": "string"}}, ["user", "role_id"])},
    {"name": "rename_channels", "description": "Rename one or more channels. Use the channel IDs from the server list. "
     "Keep emoji and separators exactly as the user asked (for example '💬│chat').",
     "input_schema": _obj({"renames": {"type": "array", "items": _obj(
         {"channel_id": {"type": "string"}, "new_name": {"type": "string"}}, ["channel_id", "new_name"])}}, ["renames"])},
    {"name": "delete_messages", "description": "Delete recent messages that contain any of the given words. "
     "Empty channel_ids means every text channel.",
     "input_schema": _obj({"channel_ids": {"type": "array", "items": {"type": "string"}},
                           "contains": {"type": "array", "items": {"type": "string"}},
                           "limit_per_channel": {"type": "integer", "description": f"messages to scan per channel, up to {MAX_DELETE_PER_CHANNEL}"}},
                          ["channel_ids", "contains", "limit_per_channel"])},
    {"name": "send_message", "description": "Send a message in a channel.",
     "input_schema": _obj({"channel_id": {"type": "string"}, "text": {"type": "string"}}, ["channel_id", "text"])},
]
for _t in TOOLS:
    _t["strict"] = True

PERMS = {"timeout_member": "moderate_members", "remove_timeout": "moderate_members", "kick_member": "kick_members",
         "ban_member": "ban_members", "add_role": "manage_roles", "remove_role": "manage_roles",
         "rename_channels": "manage_channels", "delete_messages": "manage_messages", "send_message": "send_messages"}


def server_context(guild: discord.Guild) -> str:
    """Compact list of channels and roles so Claude can pick IDs (no member list)."""
    chans = [f"{c.id} #{c.name} ({c.type.name})" + (f" in {c.category.name}" if getattr(c, "category", None) else "")
             for c in guild.channels if not isinstance(c, discord.CategoryChannel)][:150]
    roles = [f"{r.id} @{r.name}" for r in reversed(guild.roles) if not r.is_default() and not r.managed][:100]
    return ("You can act in this Discord server with the provided tools when the user asks. "
            "Every action is shown to the user for confirmation before it runs, so call the tools directly.\n"
            f"Server: {guild.name}\nChannels (id #name (type)):\n" + "\n".join(chans) +
            "\nRoles (id @name):\n" + "\n".join(roles))


def resolve_member(guild: discord.Guild, ref: str):
    ref = (ref or "").strip()
    m = re.fullmatch(r"<@!?(\d+)>", ref) or re.fullmatch(r"(\d{15,21})", ref)
    if m:
        return guild.get_member(int(m.group(1)))
    low = ref.lstrip("@").lower()
    hits = [x for x in guild.members if low in (x.name.lower(), x.display_name.lower(), str(x).lower())]
    return hits[0] if len(hits) == 1 else None


class Planned:
    def __init__(self, name: str, args: dict, line: str, error: str = ""):
        self.name, self.args, self.line, self.error = name, args, line, error


def plan(guild: discord.Guild, invoker: discord.Member, name: str, args: dict, lang: str) -> Planned:
    """Turns one tool call into a human-readable, permission-checked plan line."""
    from .moderation import hierarchy_error

    perm = PERMS.get(name)
    if perm is None:
        return Planned(name, args, name, tr("פעולה לא מוכרת", lang))
    if not getattr(invoker.guild_permissions, perm, False):
        return Planned(name, args, name, tr("אין לך הרשאה לפעולה הזו.", lang))
    if not getattr(guild.me.guild_permissions, perm, False):
        return Planned(name, args, name, tr("לבוט אין הרשאה לפעולה הזו.", lang))
    if name in ("timeout_member", "remove_timeout", "kick_member", "ban_member", "add_role", "remove_role"):
        member = resolve_member(guild, args.get("user", ""))
        if member is None:
            return Planned(name, args, name, tr("לא מצאתי את המשתמש '{user}'", lang, user=args.get("user", "")))
        args["_member"] = member
        if name in ("timeout_member", "kick_member", "ban_member") and (err := hierarchy_error(guild, invoker, member, lang)):
            return Planned(name, args, name, err)
        who = f"**{discord.utils.escape_markdown(member.display_name)}**"
        if name == "timeout_member":
            minutes = max(1, min(int(args.get("minutes") or 10), 40320))
            args["minutes"] = minutes
            return Planned(name, args, tr("🔇 השתקה של {user} ל-{n} דקות", lang, user=who, n=minutes))
        if name == "remove_timeout":
            return Planned(name, args, tr("🔊 ביטול ההשתקה של {user}", lang, user=who))
        if name == "kick_member":
            return Planned(name, args, tr("👢 הרחקה של {user}", lang, user=who))
        if name == "ban_member":
            return Planned(name, args, tr("⛔ חסימה של {user}", lang, user=who))
        role = guild.get_role(int(args["role_id"])) if str(args.get("role_id", "")).isdigit() else None
        if role is None or role >= guild.me.top_role or role.managed:
            return Planned(name, args, name, tr("לא אפשר לנהל את התפקיד הזה", lang))
        args["_role"] = role
        key = N_("➕ מתן התפקיד {role} ל-{user}") if name == "add_role" else N_("➖ הסרת התפקיד {role} מ-{user}")
        return Planned(name, args, tr(key, lang, role=f"@{role.name}", user=who))
    if name == "rename_channels":
        pairs = []
        for item in args.get("renames", [])[:50]:
            ch = guild.get_channel(int(item["channel_id"])) if str(item.get("channel_id", "")).isdigit() else None
            new = str(item.get("new_name", "")).strip()[:100]
            if ch is not None and new:
                pairs.append((ch, new))
        if not pairs:
            return Planned(name, args, name, tr("לא נמצאו ערוצים לשינוי שם", lang))
        args["_pairs"] = pairs
        lines = "\n".join(f"  • #{c.name} → {n}" for c, n in pairs[:15]) + ("\n  …" if len(pairs) > 15 else "")
        return Planned(name, args, tr("✏️ שינוי שם ל-{n} ערוצים:", lang, n=len(pairs)) + "\n" + lines)
    if name == "delete_messages":
        words = [w.strip().lower() for w in args.get("contains", []) if w.strip()][:50]
        ids = [int(c) for c in args.get("channel_ids", []) if str(c).isdigit()]
        chans = [guild.get_channel(i) for i in ids] if ids else list(guild.text_channels)
        chans = [c for c in chans if c is not None and hasattr(c, "history")]
        limit = max(1, min(int(args.get("limit_per_channel") or 100), MAX_DELETE_PER_CHANNEL))
        if not words or not chans:
            return Planned(name, args, name, tr("צריך מילים וערוצים למחיקה", lang))
        args.update(_words=words, _chans=chans, _limit=limit)
        return Planned(name, args, tr("🗑️ מחיקת הודעות שמכילות {words} מתוך {n} ההודעות האחרונות ב-{c} ערוצים",
                                      lang, words=", ".join(f"'{w}'" for w in words[:5]), n=limit, c=len(chans)))
    if name == "send_message":
        ch = guild.get_channel(int(args["channel_id"])) if str(args.get("channel_id", "")).isdigit() else None
        if ch is None or not args.get("text"):
            return Planned(name, args, name, tr("לא נמצא הערוץ", lang))
        args["_channel"] = ch
        return Planned(name, args, tr("💬 שליחת הודעה ב-#{channel}", lang, channel=ch.name))
    return Planned(name, args, name, tr("פעולה לא מוכרת", lang))


async def execute(p: Planned, invoker: discord.Member, lang: str) -> str:
    reason = f"Claude ({invoker}): {p.args.get('reason') or ''}"[:500]
    a = p.args
    try:
        if p.name == "timeout_member":
            await a["_member"].timeout(timedelta(minutes=a["minutes"]), reason=reason)
        elif p.name == "remove_timeout":
            await a["_member"].timeout(None, reason=reason)
        elif p.name == "kick_member":
            await a["_member"].kick(reason=reason)
        elif p.name == "ban_member":
            await a["_member"].guild.ban(a["_member"], reason=reason,
                                         delete_message_seconds=max(0, min(int(a.get("delete_message_days") or 0), 7)) * 86400)
        elif p.name == "add_role":
            await a["_member"].add_roles(a["_role"], reason=reason)
        elif p.name == "remove_role":
            await a["_member"].remove_roles(a["_role"], reason=reason)
        elif p.name == "rename_channels":
            for ch, new in a["_pairs"]:
                await ch.edit(name=new, reason=reason)
        elif p.name == "delete_messages":
            total = 0
            for ch in a["_chans"]:
                doomed = [m async for m in ch.history(limit=a["_limit"]) if any(w in (m.content or "").lower() for w in a["_words"])]
                for i in range(0, len(doomed), 100):
                    chunk = doomed[i:i + 100]
                    try:
                        await ch.delete_messages(chunk, reason=reason)
                    except discord.HTTPException:  # older than 14 days: one by one
                        for m in chunk:
                            await m.delete()
                total += len(doomed)
            return "✅ " + p.line + " " + tr("(נמחקו {n})", lang, n=total)
        elif p.name == "send_message":
            await a["_channel"].send(a["text"][:2000], allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        log.warning(tr("פעולה של Claude נכשלה (%s): %s"), p.name, exc.text or exc.status)
        return "❌ " + p.line.split("\n")[0] + f": {exc.text or exc.status}"
    log.info(tr("Claude ביצע פעולה: %s (אישר: %s)"), p.name, invoker)
    return "✅ " + p.line.split("\n")[0]
