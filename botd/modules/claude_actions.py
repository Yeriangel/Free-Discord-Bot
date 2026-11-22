"""Server actions Claude may request with tool use ("@bot timeout User12345", "rename all chats to ...").

Claude only *proposes* actions. The bot shows the plan with a confirm button that only the allowed user can press,
checks the user's own Discord permissions and role hierarchy, and only then runs anything.
"""

import logging
import asyncio
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
# Tools that save something in one of the bot's modules (still shown for confirmation first).
# Each is offered only when its module is on in this server.
DATA_TOOLS = [
    {"name": "add_birthday", "module": "birthdays",
     "description": "Save someone's birthday in the server's Birthdays list. Use it when the user mentions a birthday, "
                    "even in passing (for example 'John's birthday is on 24/12'). The user confirms before it is saved.",
     "input_schema": _obj({"person": {"type": "string", "description": "A member (mention, ID or exact name), or just the person's name"},
                           "date": {"type": "string", "description": "DD/MM, or DD/MM/YYYY when the year is known"}},
                          ["person", "date"])},
    {"name": "create_reminder", "module": "reminders",
     "description": "Set a reminder that the bot posts in this channel later. Use it when the user asks to be reminded "
                    "or mentions something they must do at a certain time.",
     "input_schema": _obj({"text": {"type": "string"}, "in_minutes": {"type": "integer", "description": "1 to 525600"},
                           "for_user": {"type": "string", "description": "Who to remind (mention, ID or name); empty = the user"}},
                          ["text", "in_minutes", "for_user"])},
    {"name": "start_giveaway", "module": "giveaways",
     "description": "Start a giveaway with a join button and random winners.",
     "input_schema": _obj({"prize": {"type": "string"}, "duration_minutes": {"type": "integer", "description": "1 to 86400"},
                           "winners": {"type": "integer", "description": "1 to 20"},
                           "channel_id": {"type": "string", "description": "Channel ID, or empty for this channel"}},
                          ["prize", "duration_minutes", "winners", "channel_id"])},
    {"name": "create_poll", "module": "polls",
     "description": "Post a Discord poll.",
     "input_schema": _obj({"question": {"type": "string"}, "answers": {"type": "array", "items": {"type": "string"}},
                           "hours": {"type": "integer", "description": "1 to 768"},
                           "channel_id": {"type": "string", "description": "Channel ID, or empty for this channel"}},
                          ["question", "answers", "hours", "channel_id"])},
]
# Read-only tools: answered at once, no confirmation.
READ_TOOLS = [
    {"name": "top_members", "description": "Who sent the most messages in this server (counted by the bot since it was "
                                            "added), plus levels XP when the Levels module is on.",
     "input_schema": _obj({"limit": {"type": "integer", "description": "1 to 25"}}, ["limit"])},
    {"name": "list_birthdays", "description": "The birthdays saved in this server.", "input_schema": _obj({}, [])},
    {"name": "server_info", "description": "Basic numbers about this server: members, channels, roles, boosts, creation date.",
     "input_schema": _obj({}, [])},
    {"name": "list_members", "description": "The people in this channel (everyone who can see it) or in the whole server, "
                                            "with their mention and whether they are a bot. Use it whenever you need to "
                                            "know who is here; the chat history does not show everyone.",
     "input_schema": _obj({"scope": {"type": "string", "enum": ["channel", "server"]},
                           "include_bots": {"type": "boolean"}}, ["scope", "include_bots"])},
    {"name": "pick_random_member", "description": "Picks members truly at random (the bot draws them, fairly). Always use "
                                                  "this when asked to choose someone at random, never pick by yourself.",
     "input_schema": _obj({"scope": {"type": "string", "enum": ["channel", "server"]},
                           "count": {"type": "integer", "description": "1 to 10"},
                           "include_bots": {"type": "boolean"},
                           "exclude_asker": {"type": "boolean", "description": "true to leave out the person who asked"}},
                          ["scope", "count", "include_bots", "exclude_asker"])},
]
MEMBER_TOOLS = {"list_members", "pick_random_member"}
MAX_LISTED = 150
READ_NAMES = {t["name"] for t in READ_TOOLS}
DATA_MODULES = {t["name"]: t["module"] for t in DATA_TOOLS}

for _t in TOOLS + DATA_TOOLS + READ_TOOLS:
    _t["strict"] = True


def api_tools(tools: list[dict]) -> list[dict]:
    """Tool definitions as the API wants them (without our own 'module' key)."""
    return [{k: v for k, v in t.items() if k != "module"} for t in tools]


def available_tools(bot, guild, allow_actions: bool) -> list[dict]:
    tools = list(READ_TOOLS)
    tools += [t for t in DATA_TOOLS if bot.cfg(guild, t["module"])]
    if allow_actions:
        tools += TOOLS
    return api_tools(tools)


async def load_members(guild: discord.Guild) -> None:
    """Makes sure the bot knows every member (Discord sends big member lists only on request)."""
    if not guild.chunked and guild._state._intents.members:
        try:
            await asyncio.wait_for(guild.chunk(cache=True), timeout=20)
        except Exception:  # noqa: BLE001 - answer with what is known
            pass


def _members_in(guild: discord.Guild, channel, scope: str, include_bots: bool) -> list:
    members = list(guild.members)
    if scope == "channel" and channel is not None and hasattr(channel, "permissions_for"):
        members = [m for m in members if channel.permissions_for(m).view_channel]
    if not include_bots:
        members = [m for m in members if not m.bot]
    return sorted(members, key=lambda m: m.display_name.lower())


def _member_line(m) -> str:
    extra = " (bot)" if m.bot else ""
    name = m.display_name if m.display_name == m.name else f"{m.display_name} ({m.name})"
    return f"{name} <@{m.id}>{extra}"


def _members_note(guild: discord.Guild) -> str:
    if not guild._state._intents.members:
        return ("\nNote: the Server Members intent is off, so the bot only knows members it has seen talk. "
                "Turn it on in the app (Bot page) to see everyone.")
    return ""


def run_read_tool(bot, guild: discord.Guild, name: str, args: dict, channel=None, asker=None) -> str:
    """Executes a read-only tool and returns plain text for Claude."""
    import datetime as dt
    import random

    gid = str(guild.id)
    if name in MEMBER_TOOLS:
        scope = args.get("scope") if args.get("scope") in ("channel", "server") else "channel"
        members = _members_in(guild, channel, scope, bool(args.get("include_bots")))
        where = f"#{getattr(channel, 'name', '?')}" if scope == "channel" else "the server"
        if name == "list_members":
            shown = members[:MAX_LISTED]
            more = f"\n...and {len(members) - len(shown)} more" if len(members) > len(shown) else ""
            return (f"{len(members)} members in {where}:\n" + "\n".join(_member_line(m) for m in shown) + more +
                    _members_note(guild))
        if args.get("exclude_asker") and asker is not None:
            members = [m for m in members if m.id != asker.id]
        if not members:
            return f"Nobody to pick from in {where}." + _members_note(guild)
        count = max(1, min(int(args.get("count") or 1), 10, len(members)))
        picked = random.SystemRandom().sample(members, count)
        return (f"Randomly picked from {len(members)} members in {where}: " + ", ".join(_member_line(m) for m in picked) +
                ". Mention them exactly as written." + _members_note(guild))
    if name == "top_members":
        limit = max(1, min(int(args.get("limit") or 10), 25))
        rows = bot.store.top_talkers(gid, limit)
        since = bot.store.activity_since(gid)
        if not rows:
            return "No messages counted yet. The bot counts messages only from the moment it was running in this server."
        head = f"Messages counted since {dt.datetime.fromtimestamp(since):%Y-%m-%d} (only while the bot was online):"
        lines = [f"{i}. {r['user_name']} (<@{r['user_id']}>): {r['messages']} messages" for i, r in enumerate(rows, 1)]
        if bot.cfg(guild, "levels"):
            lines.append("Levels XP leaderboard: " + ", ".join(
                f"{r['user_name']} {r['xp']} XP" for r in bot.store.leaderboard(gid, limit=min(limit, 10))))
        return head + "\n" + "\n".join(lines)
    if name == "list_birthdays":
        rows = bot.store.list_birthdays(gid)
        return "\n".join(f"{r['user_name']}: {r['day']:02d}/{r['month']:02d}" + (f"/{r['year']}" if r["year"] else "")
                         for r in rows) or "No birthdays saved."
    if name == "server_info":
        created = guild.created_at.strftime("%Y-%m-%d") if getattr(guild, "created_at", None) else "?"
        return (f"Name: {guild.name}\nMembers: {guild.member_count}\nChannels: {len(guild.channels)}\n"
                f"Roles: {max(0, len(guild.roles) - 1)}\nBoosts: {guild.premium_subscription_count or 0}\nCreated: {created}")
    return "Unknown tool"

PERMS = {"timeout_member": "moderate_members", "remove_timeout": "moderate_members", "kick_member": "kick_members",
         "ban_member": "ban_members", "add_role": "manage_roles", "remove_role": "manage_roles",
         "rename_channels": "manage_channels", "delete_messages": "manage_messages", "send_message": "send_messages"}


def server_context(guild: discord.Guild, tool_names: list[str] | None = None) -> str:
    """What Claude knows about the server: today's date, how the tools work, channels and roles (no member list)."""
    import datetime as dt

    tool_names = tool_names or [t["name"] for t in TOOLS]
    chans = [f"{c.id} #{c.name} ({c.type.name})" + (f" in {c.category.name}" if getattr(c, "category", None) else "")
             for c in guild.channels if not isinstance(c, discord.CategoryChannel)][:150]
    roles = [f"{r.id} @{r.name}" for r in reversed(guild.roles) if not r.is_default() and not r.managed][:100]
    today = dt.date.today()
    text = (f"Today is {today:%A, %d %B %Y}. You are in the Discord server '{guild.name}'.\n"
            "Tools that change something are never run directly: the bot first shows the user what will happen, "
            "with a confirm button (the user can also type yes/no). So when the user asks for an action, call the tool "
            "right away instead of asking in text.\n")
    saving = [n for n in tool_names if n in DATA_MODULES]
    if saving:
        text += ("When the user mentions something one of these tools can save (" + ", ".join(saving) + "), for example "
                 "'John's birthday is on 24/12', call that tool so the user can choose to save it, and say in one short "
                 "sentence that you can add it. Never invent dates or details the user did not give.\n")
    if any(n in READ_NAMES for n in tool_names):
        text += ("Use the read tools (" + ", ".join(sorted(READ_NAMES)) + ") to answer questions about the server. "
                 "You do not see the member list unless you call list_members; to choose someone at random call "
                 "pick_random_member.\n")
    return (text + f"Server: {guild.name}\nChannels (id #name (type)):\n" + "\n".join(chans) +
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


def _plan_data(guild: discord.Guild, invoker: discord.Member, name: str, args: dict, lang: str, bot, channel) -> Planned:
    """Plan lines for the module tools (birthdays, reminders, giveaways, polls)."""
    import time

    from .common import has_any_role, relative_time

    module = DATA_MODULES[name]
    cfg = bot.cfg(guild, module) if bot is not None else None
    if not cfg:
        return Planned(name, args, name, tr("המודול כבוי בשרת הזה. אפשר להפעיל אותו באפליקציה.", lang))
    args.update(_bot=bot, _cfg=cfg)

    def target_channel():
        cid = str(args.get("channel_id") or "").strip()
        ch = guild.get_channel(int(cid)) if cid.isdigit() else channel
        return ch if ch is not None and hasattr(ch, "send") else None

    if name == "add_birthday":
        from .birthdays import fmt_date, parse_date

        parsed = parse_date(str(args.get("date", "")))
        if parsed is None:
            return Planned(name, args, name, tr("תאריך לא תקין: {date}", lang, date=args.get("date", "")))
        person = str(args.get("person", "")).strip()[:100]
        member = resolve_member(guild, person)
        if not person:
            return Planned(name, args, name, tr("חסר שם", lang))
        args.update(_member=member, _parsed=parsed)
        who = member.display_name if member else person
        date = fmt_date({"month": parsed[0], "day": parsed[1], "year": parsed[2]})
        return Planned(name, args, tr("🎂 להוסיף לימי ההולדת: **{name}**, {date}", lang,
                                      name=discord.utils.escape_markdown(who), date=date))
    if name == "create_reminder":
        minutes = max(1, min(int(args.get("in_minutes") or 0), 525600))
        ref = str(args.get("for_user") or "").strip()
        member = resolve_member(guild, ref) if ref else invoker
        if member is None:
            return Planned(name, args, name, tr("לא מצאתי את המשתמש '{user}'", lang, user=ref))
        if channel is None:
            return Planned(name, args, name, tr("לא נמצא הערוץ", lang))
        due = time.time() + minutes * 60
        args.update(_member=member, _due=due, _channel=channel)
        return Planned(name, args, tr("⏰ תזכורת ל-{user} {when}: {message}", lang, user=f"**{discord.utils.escape_markdown(member.display_name)}**",
                                      when=relative_time(due), message=str(args.get("text", ""))[:200]))
    if name == "start_giveaway":
        if not (invoker.guild_permissions.manage_guild or has_any_role(invoker, cfg["manager_role_ids"])):
            return Planned(name, args, name, tr("אין לך הרשאה לפעולה הזו.", lang))
        ch = target_channel()
        if ch is None:
            return Planned(name, args, name, tr("לא נמצא הערוץ", lang))
        minutes = max(1, min(int(args.get("duration_minutes") or 60), 86400))
        winners = max(1, min(int(args.get("winners") or 1), 20))
        args.update(_channel=ch, _seconds=minutes * 60, _winners=winners)
        from .common import human_duration

        return Planned(name, args, tr("🎉 הגרלה על **{prize}** ב-#{channel}: {n} זוכים, למשך {duration}", lang,
                                      prize=str(args.get("prize", ""))[:200], channel=ch.name, n=winners,
                                      duration=human_duration(minutes * 60, lang)))
    if name == "create_poll":
        ch = target_channel()
        answers = [str(a).strip()[:55] for a in args.get("answers", []) if str(a).strip()][:10]
        if ch is None or len(answers) < 2 or not str(args.get("question", "")).strip():
            return Planned(name, args, name, tr("צריך שאלה, לפחות 2 תשובות וערוץ", lang))
        args.update(_channel=ch, _answers=answers)
        return Planned(name, args, tr("📊 סקר ב-#{channel}: {question} ({answers})", lang, channel=ch.name,
                                      question=str(args["question"])[:200], answers=" / ".join(answers)))
    return Planned(name, args, name, tr("פעולה לא מוכרת", lang))


async def _execute_data(p: Planned, invoker: discord.Member, lang: str) -> str:
    a = p.args
    bot, guild = a["_bot"], invoker.guild
    if p.name == "add_birthday":
        member = a["_member"]
        bot.store.set_birthday(str(guild.id), str(member.id) if member else "",
                               member.display_name if member else str(a["person"]).strip()[:100], *a["_parsed"])
    elif p.name == "create_reminder":
        bot.store.add_reminder(str(guild.id), str(a["_channel"].id), str(a["_member"].id), str(a["text"])[:1000], a["_due"])
    elif p.name == "start_giveaway":
        cog = bot.get_cog("Giveaways")
        await cog.start(guild, a["_channel"], invoker, str(a["prize"])[:200], a["_seconds"], a["_winners"])
    elif p.name == "create_poll":
        from .polls import build_poll

        await a["_channel"].send(poll=build_poll(str(a["question"]), a["_answers"], min(int(a.get("hours") or 24), a["_cfg"]["max_hours"])))
    log.info(tr("Claude ביצע פעולה: %s (אישר: %s)"), p.name, invoker)
    return "✅ " + p.line.split("\n")[0]


def plan(guild: discord.Guild, invoker: discord.Member, name: str, args: dict, lang: str, bot=None,
         channel=None) -> Planned:
    """Turns one tool call into a human-readable, permission-checked plan line."""
    from .moderation import hierarchy_error

    if name in DATA_MODULES:
        return _plan_data(guild, invoker, name, args, lang, bot, channel)
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
    if p.name in DATA_MODULES:
        try:
            return await _execute_data(p, invoker, lang)
        except (discord.HTTPException, ValueError) as exc:
            text = getattr(exc, "text", None) or str(exc)
            log.warning(tr("פעולה של Claude נכשלה (%s): %s"), p.name, text)
            return "❌ " + p.line.split("\n")[0] + f": {text}"
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
