"""Automations, MEE6-style: WHEN SOMEONE (trigger) -> IF (conditions, all must pass) -> DO THIS (actions in order).

This module defines what is allowed and validates the user's automations. The engine is in modules/automations.py.
"""

import re
import secrets

from .i18n import N_, tr
from .store import valid_snowflake

MAX_AUTOMATIONS = 50
MAX_CONDITIONS = 10
MAX_ACTIONS = 8

MSG = ("message_send", "message_edit", "message_delete")
TRIGGERS = {
    "message_send": N_("שולח הודעה"),
    "message_delete": N_("מוחק הודעה"),
    "message_edit": N_("עורך הודעה"),
    "button_click": N_("לוחץ על כפתור"),
    "reaction_add": N_("מוסיף ריאקשן"),
    "role_gain": N_("מקבל תפקיד"),
    "role_lose": N_("מאבד תפקיד"),
    "voice_join": N_("מצטרף לערוץ קול"),
    "voice_leave": N_("עוזב ערוץ קול"),
    "thread_create": N_("פותח שרשור"),
    "member_join": N_("מצטרף לשרת"),
}

ROLE_TRIGGERS = tuple(TRIGGERS)
# condition type -> (label, value kind, triggers it applies to)
CONDITIONS = {
    "in_channels": (N_("זה קרה באחד מהערוצים או הקטגוריות האלה"), "channels",
                    MSG + ("button_click", "reaction_add", "voice_join", "voice_leave", "thread_create")),
    "has_any_role": (N_("למשתמש יש אחד מהתפקידים האלה"), "roles", ROLE_TRIGGERS),
    "has_all_roles": (N_("למשתמש יש את כל התפקידים האלה"), "roles", ROLE_TRIGGERS),
    "lacks_any_role": (N_("למשתמש אין אחד מהתפקידים האלה"), "roles", ROLE_TRIGGERS),
    "lacks_all_roles": (N_("למשתמש אין אף אחד מהתפקידים האלה"), "roles", ROLE_TRIGGERS),
    "user_is": (N_("המשתמש הוא אחד מהמשתמשים האלה"), "users", ROLE_TRIGGERS),
    "content_contains": (N_("ההודעה מכילה אחת מהמילים או המשפטים האלה"), "words", MSG),
    "content_exact": (N_("ההודעה היא בדיוק אחת מהמילים או המשפטים האלה"), "words", MSG),
    "content_starts": (N_("ההודעה מתחילה באחת מהמילים האלה"), "words", MSG),
    "content_not_contains": (N_("ההודעה לא מכילה אף אחת מהמילים או המשפטים האלה"), "words", MSG),
    "reacted_with": (N_("המשתמש הגיב עם אחד מהאימוג'ים האלה"), "emojis", ("reaction_add",)),
    "button_is": (N_("הכפתור הוא אחד מהכפתורים האלה"), "words", ("button_click",)),
    "gained_role": (N_("המשתמש קיבל אחד מהתפקידים האלה"), "roles", ("role_gain",)),
    "lost_role": (N_("המשתמש איבד אחד מהתפקידים האלה"), "roles", ("role_lose",)),
    "has_image": (N_("בהודעה יש תמונה מצורפת"), "none", MSG),
    "has_video": (N_("בהודעה יש סרטון מצורף"), "none", MSG),
    "has_audio": (N_("בהודעה יש קובץ שמע מצורף"), "none", MSG),
    "has_text_file": (N_("בהודעה יש קובץ טקסט מצורף"), "none", MSG),
    "is_reply": (N_("ההודעה היא תגובה"), "none", MSG),
    "is_not_reply": (N_("ההודעה היא לא תגובה"), "none", MSG),
}

# action type -> (label, needs a message to act on)
ACTIONS = {
    "send_message": (N_("שליחת הודעה"), False),
    "reply": (N_("תגובה להודעה"), True),
    "add_roles": (N_("מתן התפקידים האלה למשתמש"), False),
    "remove_roles": (N_("הסרת התפקידים האלה מהמשתמש"), False),
    "give_coins": (N_("מתן מטבעות למשתמש"), False),
    "take_coins": (N_("לקיחת מטבעות מהמשתמש"), False),
    "give_xp": (N_("מתן XP למשתמש"), False),
    "take_xp": (N_("לקיחת XP מהמשתמש"), False),
    "add_reaction": (N_("הוספת ריאקשן"), True),
    "pin_message": (N_("נעיצת ההודעה"), True),
    "create_thread": (N_("פתיחת שרשור על ההודעה"), True),
    "delete_message": (N_("מחיקת ההודעה"), True),
}
TARGETS = {"same_channel": N_("באותו ערוץ"), "channel": N_("בערוץ מסוים"), "dm": N_("בהודעה פרטית")}
# triggers that come with a message the actions can act on (a deleted message cannot be acted on)
MESSAGE_TRIGGERS = ("message_send", "message_edit", "reaction_add", "button_click")
CHANNEL_TRIGGERS = MSG + ("reaction_add", "button_click", "thread_create")


def _sf(value, label: str, required: bool = False) -> str:
    value = str(value or "").strip()
    if not valid_snowflake(value) or (required and not value):
        if required and not value:
            raise ValueError(tr("{label}: צריך לבחור", label=label))
        raise ValueError(tr("{label}: מזהה לא תקין", label=label))
    return value


def _text(value, label: str, limit: int, required: bool = False) -> str:
    value = str(value or "")
    if len(value) > limit:
        raise ValueError(tr("{label}: עד {n} תווים", label=label, n=limit))
    if required and not value.strip():
        raise ValueError(tr("{label}: שדה חובה", label=label))
    return value


def _values(kind: str, raw, label: str) -> list:
    items = raw if isinstance(raw, list) else str(raw or "").splitlines()
    items = [str(v).strip() for v in items if str(v).strip()]
    if kind in ("channels", "roles", "users"):
        items = [_sf(v, label) for v in items]
    elif any(len(v) > 200 for v in items):
        raise ValueError(tr("{label}: עד {n} תווים", label=label, n=200))
    if not items:
        raise ValueError(tr("{label}: צריך לבחור", label=label))
    if len(items) > 50:
        raise ValueError(tr("{label}: יותר מדי פריטים", label=label))
    return list(dict.fromkeys(items))


def migrate_v1(raw: dict) -> dict:
    """Converts automations saved by the first version (one trigger object) to the current shape."""
    t = raw.get("trigger")
    if not isinstance(t, dict):
        return raw
    out = {k: raw[k] for k in ("id", "name", "enabled", "cooldown") if k in raw}
    conds, kind = [], t.get("type")
    if t.get("channel_id"):
        conds.append({"type": "in_channels", "values": [t["channel_id"]]})
    if kind == "message":
        out["trigger"] = "message_send"
        match = t.get("match", "any")
        if match != "any" and t.get("text"):
            conds.append({"type": {"exact": "content_exact", "starts_with": "content_starts"}.get(match, "content_contains"),
                          "values": [t["text"]]})
    elif kind == "reaction_add":
        out["trigger"] = "reaction_add"
        if t.get("emoji"):
            conds.append({"type": "reacted_with", "values": [t["emoji"]]})
    elif kind in ("role_added", "role_removed"):
        out["trigger"] = "role_gain" if kind == "role_added" else "role_lose"
        if t.get("role_id"):
            conds.append({"type": "gained_role" if kind == "role_added" else "lost_role", "values": [t["role_id"]]})
    else:
        out["trigger"] = "member_join"
    c = raw.get("conditions") or {}
    if isinstance(c, dict):
        if c.get("required_role_id"):
            conds.append({"type": "has_any_role", "values": [c["required_role_id"]]})
        out["ignore_bots"] = c.get("ignore_bots", True)
    out["conditions"] = conds
    acts = []
    for a in raw.get("actions") or []:
        a = dict(a)
        if a.get("type") in ("add_role", "remove_role"):
            a = {"type": a["type"] + "s", "roles": [a.get("role_id")] if a.get("role_id") else []}
        acts.append(a)
    out["actions"] = acts
    return out


def validate_automation(raw: dict, index: int) -> dict:
    raw = migrate_v1(raw)
    fallback = tr("אוטומציה {n}", n=index + 1)
    name = _text(raw.get("name") or fallback, tr("שם"), 60).strip() or fallback
    where = tr("באוטומציה '{name}'", name=name)

    def lbl(text: str) -> str:
        return f"{where}, {tr(text)}"

    def fail(text: str, **kw):
        raise ValueError(f"{where}: {tr(text, **kw)}")

    trigger = raw.get("trigger")
    if trigger not in TRIGGERS:
        fail("צריך לבחור מה מפעיל את האוטומציה")

    conditions = []
    for c in (raw.get("conditions") or [])[:MAX_CONDITIONS + 1]:
        ctype = c.get("type") if isinstance(c, dict) else None
        if ctype not in CONDITIONS:
            fail("סוג תנאי לא חוקי")
        label, kind, allowed = CONDITIONS[ctype]
        if trigger not in allowed:
            fail("התנאי '{cond}' לא מתאים לאירוע הזה", cond=tr(label))
        cond = {"type": ctype}
        if kind != "none":
            cond["values"] = _values(kind, c.get("values"), lbl(label))
        conditions.append(cond)
    if len(conditions) > MAX_CONDITIONS:
        fail("עד {n} תנאים", n=MAX_CONDITIONS)

    actions = []
    for a in (raw.get("actions") or [])[:MAX_ACTIONS + 1]:
        atype = a.get("type") if isinstance(a, dict) else None
        if atype not in ACTIONS:
            fail("סוג פעולה לא חוקי")
        label, needs_message = ACTIONS[atype]
        if needs_message and trigger not in MESSAGE_TRIGGERS:
            fail("הפעולה '{action}' עובדת רק עם אירוע שיש בו הודעה", action=tr(label))
        act = {"type": atype}
        if atype in ("send_message", "reply"):
            act["text"] = _text(a.get("text"), lbl("טקסט ההודעה"), 1800, required=True)
        if atype == "send_message":
            target = a.get("target", "same_channel")
            if target not in TARGETS:
                fail("יעד לא חוקי")
            if target == "same_channel" and trigger not in CHANNEL_TRIGGERS:
                fail("'באותו ערוץ' לא מתאים לאירוע הזה. בחר ערוץ מסוים.")
            act["target"] = target
            act["channel_id"] = _sf(a.get("channel_id"), lbl("ערוץ היעד"), required=target == "channel")
            raw_buttons = a.get("buttons") or []
            buttons = raw_buttons if isinstance(raw_buttons, list) else str(raw_buttons).splitlines()
            buttons = [str(b).strip() for b in buttons if str(b).strip()]
            if len(buttons) > 5 or any(len(b) > 80 for b in buttons):
                fail("עד 5 כפתורים, עד 80 תווים לכפתור")
            act["buttons"] = buttons
        if atype in ("add_roles", "remove_roles"):
            act["roles"] = _values("roles", a.get("roles"), lbl("תפקידים"))
        if atype in ("give_coins", "take_coins", "give_xp", "take_xp"):
            try:
                amount = int(a.get("amount", 0))
            except (TypeError, ValueError):
                fail("כמות צריכה להיות מספר")
            if not 1 <= amount <= 1_000_000:
                fail("כמות בין 1 ל-1,000,000")
            act["amount"] = amount
        if atype == "add_reaction":
            act["emoji"] = _text(a.get("emoji"), lbl("אימוג'י"), 64, required=True).strip()
        if atype == "create_thread":
            act["name"] = _text(a.get("name") or tr("דיון"), lbl("שם השרשור"), 90)
        actions.append(act)
    if not actions:
        fail("צריך לפחות פעולה אחת")
    if len(actions) > MAX_ACTIONS:
        fail("עד {n} פעולות", n=MAX_ACTIONS)

    try:
        cooldown = int(raw.get("cooldown", 0) or 0)
    except (TypeError, ValueError):
        fail("זמן המתנה צריך להיות מספר")
    if not 0 <= cooldown <= 86400:
        fail("זמן המתנה בין 0 ל-86400 שניות")
    aid = str(raw.get("id") or "")
    if not re.fullmatch(r"[a-f0-9]{8}", aid):
        aid = secrets.token_hex(4)
    return {"id": aid, "name": name, "enabled": bool(raw.get("enabled", True)), "trigger": trigger,
            "conditions": conditions, "actions": actions, "cooldown": cooldown,
            "ignore_bots": bool(raw.get("ignore_bots", True)), "retry": bool(raw.get("retry", False))}


def validate_automations(value) -> list[dict]:
    items = value if isinstance(value, list) else []
    if len(items) > MAX_AUTOMATIONS:
        raise ValueError(tr("עד {n} אוטומציות", n=MAX_AUTOMATIONS))
    out, ids = [], set()
    for i, raw in enumerate(items):
        auto = validate_automation(raw if isinstance(raw, dict) else {}, i)
        while auto["id"] in ids:  # duplicated automations get a fresh id
            auto["id"] = secrets.token_hex(4)
        ids.add(auto["id"])
        out.append(auto)
    return out


def schema_json() -> dict:
    return {
        "triggers": dict(TRIGGERS),
        "conditions": {k: {"label": v[0], "kind": v[1], "triggers": list(v[2])} for k, v in CONDITIONS.items()},
        "actions": {k: {"label": v[0], "needs_message": v[1]} for k, v in ACTIONS.items()},
        "targets": dict(TARGETS),
        "message_triggers": list(MESSAGE_TRIGGERS), "channel_triggers": list(CHANNEL_TRIGGERS),
        "max_automations": MAX_AUTOMATIONS, "max_conditions": MAX_CONDITIONS, "max_actions": MAX_ACTIONS,
    }
