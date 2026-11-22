"""The app's Console: typed commands to control the bot (help, status, say, dm, module on/off, ...)."""

import logging
import shlex

import discord

from . import catalog
from .i18n import N_, tr

log = logging.getLogger("botd.console")

HELP = [
    ("help", N_("הרשימה הזו")),
    ("status", N_("מצב הבוט, שרתים ו-Ping")),
    ("start / stop / restart", N_("הפעלה, כיבוי או הפעלה מחדש של הבוט")),
    ("servers", N_("השרתים של הבוט")),
    (N_("channels <שרת>"), N_("ערוצי הטקסט של שרת (שם או מזהה)")),
    (N_("say <ערוץ> <טקסט>"), N_("שליחת הודעה בשם הבוט (מזהה ערוץ, או #שם)")),
    (N_("dm <מזהה משתמש> <טקסט>"), N_("הודעה פרטית בשם הבוט")),
    (N_("modules <שרת>"), N_("אילו מודולים פעילים בשרת")),
    (N_("module <שרת> <מודול> on|off"), N_("הפעלה או כיבוי של מודול")),
    ("clear", N_("ניקוי המסך")),
]


def _bots(manager) -> list:
    if hasattr(manager, "all_bots"):
        return manager.all_bots()
    return [manager.bot] if manager.bot is not None and manager.state == "connected" else []


def _all_guilds(manager) -> list:
    seen, out = set(), []
    for bot in _bots(manager):
        for g in bot.guilds:
            if g.id not in seen:
                seen.add(g.id)
                out.append(g)
    return out


def _guild(manager, ref: str):
    guilds = _all_guilds(manager)
    if not guilds:
        return None
    ref = ref.strip().lower()
    for g in guilds:
        if ref in (str(g.id), g.name.lower()):
            return g
    matches = [g for g in guilds if ref in g.name.lower()]
    return matches[0] if len(matches) == 1 else None


def _channel(manager, ref: str):
    ref = ref.strip().lstrip("#")
    if ref.startswith("<#") and ref.endswith(">"):
        ref = ref[2:-1]
    if ref.isdigit():
        for bot in _bots(manager):
            ch = bot.get_channel(int(ref))
            if ch is not None and hasattr(ch, "send"):
                return ch
        return None
    matches = [c for g in _all_guilds(manager) for c in g.text_channels if c.name.lower() == ref.lower()]
    return matches[0] if len(matches) == 1 else None


async def run_command(manager, store, line: str) -> list[str]:
    line = (line or "").strip()
    if not line:
        return []
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    cmd, args = parts[0].lower(), parts[1:]
    online = bool(_bots(manager))
    need_online = tr("הבוט לא מחובר. הקלד start כדי להפעיל אותו.")

    if cmd in ("help", "?"):
        return [f"{tr(name):<34} {tr(desc)}" for name, desc in HELP]
    if cmd == "status":
        st = manager.status()
        out = [tr("מצב: {state}", state=st["state"])]
        if st["user"]:
            out.append(tr("מחובר כ-{name}", name=st["user"]["name"]))
        if st["guild_count"] is not None:
            out.append(tr("שרתים: {n} · Ping: {ms}ms", n=st["guild_count"], ms=st["latency_ms"]))
        if st["intents"]:
            out.append("Intents: " + ", ".join(f"{k} {'✓' if v else '✕'}" for k, v in st["intents"].items()))
        return out
    if cmd in ("start", "stop", "restart"):
        try:
            if cmd in ("stop", "restart"):
                await manager.stop()
            if cmd in ("start", "restart"):
                await manager.start()
        except ValueError as exc:
            return [str(exc)]
        return [tr("✓ בוצע. מצב: {state}", state=manager.state)]
    if cmd == "servers":
        if not online:
            return [need_online]
        return [f"{g.id}  {g.name}  ({g.member_count})" for g in _all_guilds(manager)] or [tr("הבוט לא נמצא באף שרת.")]
    if cmd == "channels":
        if not online:
            return [need_online]
        guilds = _all_guilds(manager)
        guild = _guild(manager, " ".join(args)) if args else (guilds[0] if len(guilds) == 1 else None)
        if guild is None:
            return [tr("לא מצאתי את השרת. כתוב channels ואז שם או מזהה של שרת.")]
        return [f"{c.id}  #{c.name}" + (f"  · {c.category.name}" if c.category else "") for c in guild.text_channels]
    if cmd == "say":
        if not online:
            return [need_online]
        if len(args) < 2:
            return [tr("שימוש: say <ערוץ> <טקסט>")]
        channel = _channel(manager, args[0])
        if channel is None:
            return [tr("לא מצאתי את הערוץ (או שיש כמה בשם הזה). השתמש במזהה הערוץ.")]
        text = line.split(None, 2)[2] if len(line.split(None, 2)) > 2 else " ".join(args[1:])
        try:
            await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
        except discord.HTTPException as exc:
            return [tr("דיסקורד דחה את ההודעה: {error}", error=exc.text or exc.status)]
        log.info(tr("נשלחה הודעה מהקונסול ל-#%s בשרת %s"), channel.name, channel.guild.name)
        return [tr("✓ נשלח ל-#{channel}", channel=channel.name)]
    if cmd == "dm":
        if not online:
            return [need_online]
        if len(args) < 2 or not args[0].isdigit():
            return [tr("שימוש: dm <מזהה משתמש> <טקסט>")]
        try:
            user = manager.bot.get_user(int(args[0])) or await manager.bot.fetch_user(int(args[0]))
            await user.send(line.split(None, 2)[2][:2000])
        except discord.HTTPException as exc:
            return [tr("ההודעה הפרטית נכשלה: {error}", error=exc.text or exc.status)]
        return [tr("✓ נשלח ל-{user}", user=str(user))]
    if cmd == "modules":
        guild_id = _resolve_gid(manager, store, " ".join(args))
        if not guild_id:
            return [tr("לא מצאתי את השרת.")]
        g = store.get_guild(guild_id)
        on = [m["key"] for m in catalog.MODULES if g["modules"].get(m["key"])]
        return [tr("פעילים ב-{server}: {list}", server=g["name"], list=", ".join(on) or "-")]
    if cmd == "module":
        if len(args) < 3 or args[-1].lower() not in ("on", "off"):
            return [tr("שימוש: module <שרת> <מודול> on|off")]
        key, state = args[-2], args[-1].lower() == "on"
        guild_id = _resolve_gid(manager, store, " ".join(args[:-2]))
        if not guild_id:
            return [tr("לא מצאתי את השרת.")]
        if key not in catalog.MODULE_KEYS:
            return [tr("מודול לא מוכר: {key}. הקלד modules כדי לראות את השמות.", key=key)]
        store.set_module(guild_id, key, state)
        return [tr("✓ {key} {state} ב-{server}", key=key, state="on" if state else "off", server=store.get_guild(guild_id)["name"])]
    return [tr("פקודה לא מוכרת: {cmd}. הקלד help.", cmd=cmd)]


def _resolve_gid(manager, store, ref: str) -> str | None:
    ref = ref.strip().lower()
    guilds = store.list_guilds()
    if not ref and len(guilds) == 1:
        return guilds[0]["id"]
    for g in guilds:
        if ref in (g["id"], g["name"].lower()):
            return g["id"]
    matches = [g for g in guilds if ref and ref in g["name"].lower()]
    return matches[0]["id"] if len(matches) == 1 else None
