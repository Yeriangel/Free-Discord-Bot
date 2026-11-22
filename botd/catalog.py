"""Static catalogs: Discord permission bits, modules grouped MEE6-style, and per-module settings schemas."""

from .i18n import tr
import re
from urllib.parse import urlencode

from .store import valid_snowflake

# (key, bit, Hebrew label, dangerous?)
PERMISSIONS = [
    ("view_channel", 1 << 10, "צפייה בערוצים", False),
    ("send_messages", 1 << 11, "שליחת הודעות", False),
    ("send_messages_in_threads", 1 << 38, "שליחת הודעות בשרשורים", False),
    ("embed_links", 1 << 14, "הטמעת קישורים", False),
    ("attach_files", 1 << 15, "צירוף קבצים", False),
    ("read_message_history", 1 << 16, "קריאת היסטוריית הודעות", False),
    ("add_reactions", 1 << 6, "הוספת ריאקשנים", False),
    ("use_external_emojis", 1 << 18, "אימוג'י חיצוניים", False),
    ("connect", 1 << 20, "התחברות לערוצי קול", False),
    ("create_instant_invite", 1 << 0, "יצירת הזמנות", False),
    ("manage_messages", 1 << 13, "ניהול הודעות (מחיקה)", True),
    ("manage_threads", 1 << 34, "ניהול שרשורים", True),
    ("create_public_threads", 1 << 35, "יצירת שרשורים", False),
    ("create_private_threads", 1 << 36, "יצירת שרשורים פרטיים", False),
    ("send_polls", 1 << 49, "יצירת סקרים", False),
    ("manage_nicknames", 1 << 27, "ניהול כינויים", True),
    ("manage_roles", 1 << 28, "ניהול תפקידים", True),
    ("manage_channels", 1 << 4, "ניהול ערוצים", True),
    ("manage_webhooks", 1 << 29, "ניהול Webhooks", True),
    ("move_members", 1 << 24, "העברת חברים בין ערוצי קול", True),
    ("view_audit_log", 1 << 7, "צפייה ביומן הביקורת", False),
    ("moderate_members", 1 << 40, "השתקה זמנית (Timeout)", True),
    ("kick_members", 1 << 1, "הרחקת חברים", True),
    ("ban_members", 1 << 2, "חסימת חברים", True),
    ("mention_everyone", 1 << 17, "תיוג ‎@everyone", True),
    ("manage_guild", 1 << 5, "ניהול השרת", True),
    ("administrator", 1 << 3, "Administrator (הכל, לא מומלץ)", True),
]
PERM_BITS = {key: bit for key, bit, _, _ in PERMISSIONS}

BASE_PERMS = ["view_channel", "send_messages", "embed_links", "read_message_history"]

INTENT_LABELS = {"members": "Server Members Intent", "message_content": "Message Content Intent"}

from .catalog_modules import CATEGORIES, LOG_EVENTS, MODULES  # noqa: E402,F401

MODULES_BY_KEY = {m["key"]: m for m in MODULES}
MODULE_KEYS = set(MODULES_BY_KEY)


# ---- config defaults & validation ------------------------------------------
def module_defaults(key: str, lang: str = "he") -> dict:
    import copy

    out = {}
    for f in MODULES_BY_KEY[key]["fields"]:
        value = copy.deepcopy(f["default"])
        if lang != "he" and isinstance(value, str) and f["type"] in ("text", "textarea"):
            value = tr(value, lang)
        elif lang != "he" and f["type"] == "list" and isinstance(value, list):
            texts = {i["key"] for i in f.get("item_fields", []) if i["type"] in ("text", "textarea")}
            value = [{k: (tr(v, lang) if k in texts and isinstance(v, str) else v) for k, v in row.items()} for row in value]
        out[f["key"]] = value
    return out


def strip_defaults(key: str, raw: dict | None) -> dict:
    """Drops saved texts that are just a default message (in any language), so they follow the bot's language.
    Texts the user wrote themselves are kept."""
    from .i18n import LANGS

    raw = dict(raw or {})
    for f in MODULES_BY_KEY[key]["fields"]:
        default = f.get("default")
        if f["type"] in ("text", "textarea") and isinstance(default, str) and default and f["key"] in raw:
            if str(raw[f["key"]]).strip() in {tr(default, lang).strip() for lang in LANGS}:
                del raw[f["key"]]
        elif f["type"] == "list" and default and f["key"] in raw:
            if any(raw[f["key"]] == module_defaults(key, lang)[f["key"]] for lang in LANGS):
                del raw[f["key"]]
    return raw


def merged_config(key: str, raw: dict | None, lang: str = "he") -> dict:
    """Stored config on top of defaults (unknown keys dropped). Text defaults follow the server's bot language."""
    config = module_defaults(key, lang)
    for k, v in (raw or {}).items():
        if k in config:
            config[k] = v
    return config


_CMD_NAME = re.compile(r"^[\w\-]{1,32}$", re.UNICODE)


def _snowflake(value, label: str) -> str:
    value = str(value or "").strip()
    if not valid_snowflake(value):
        raise ValueError(tr("{label}: מזהה לא תקין", label=label))
    return value


def _validate_field(field: dict, value):
    t, label = field["type"], field.get("_label") or tr(field["label"])
    if t == "text":
        value = str(value or "").strip()
        if len(value) > field.get("max", 200):
            raise ValueError(tr("{label}: ארוך מדי", label=label))
        return value
    if t == "textarea":
        value = str(value or "")
        if len(value) > 1800:
            raise ValueError(tr("{label}: ארוך מדי (עד 1800 תווים)", label=label))
        return value
    if t == "bool":
        return bool(value)
    if t == "number":
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError(tr("{label}: צריך להיות מספר", label=label)) from None
        if not field.get("min", value) <= value <= field.get("max", value):
            raise ValueError(tr("{label}: בין {min} ל-{max}", label=label, min=field.get('min'), max=field.get('max')))
        return value
    if t == "select":
        allowed = [o[0] for o in field["options"]]
        if value not in allowed:
            raise ValueError(tr("{label}: ערך לא חוקי", label=label))
        return value
    if t in ("channel", "role", "voice_channel", "category"):
        return _snowflake(value, label)
    if t in ("channels", "roles"):
        items = [_snowflake(v, label) for v in (value or []) if str(v or "").strip()]
        if len(items) > 50:
            raise ValueError(tr("{label}: יותר מדי פריטים", label=label))
        return list(dict.fromkeys(items))
    if t == "lines":
        if isinstance(value, str):
            value = value.splitlines()
        items = [str(v).strip() for v in (value or []) if str(v).strip()]
        if len(items) > 500 or any(len(v) > 100 for v in items):
            raise ValueError(tr("{label}: עד 500 שורות, עד 100 תווים בשורה", label=label))
        return list(dict.fromkeys(items))
    if t == "commands":
        out, seen = [], set()
        for item in value or []:
            name = str(item.get("name", "")).strip().lower().lstrip("!")
            response = str(item.get("response", ""))
            if not name and not response.strip() and not item.get("role_id"):
                continue
            if not _CMD_NAME.match(name):
                raise ValueError(tr("שם פקודה לא תקין: '{name}' (אותיות, מספרים, _ או -, עד 32 תווים)", name=name))
            if name in seen:
                raise ValueError(tr("הפקודה '{name}' מופיעה פעמיים", name=name))
            if len(response) > 1800 or (not response.strip() and (item.get("role_action") or "none") == "none"):
                raise ValueError(tr("לפקודה '{name}' צריכה להיות תשובה או פעולת תפקיד (עד 1800 תווים)", name=name))
            role_id = _snowflake(item.get("role_id", ""), tr("תפקיד בפקודה '{name}'", name=name))
            role_action = item.get("role_action") or "none"
            if role_action not in ("none", "add", "remove", "toggle"):
                raise ValueError(tr("פעולת תפקיד לא חוקית בפקודה '{name}'", name=name))
            if role_action != "none" and not role_id:
                raise ValueError(tr("בפקודה '{name}' נבחרה פעולת תפקיד בלי תפקיד", name=name))
            kind = item.get("kind") or "prefix"
            send_mode = item.get("send_mode") or "channel"
            if kind not in ("prefix", "slash", "both") or send_mode not in ("channel", "reply", "dm"):
                raise ValueError(tr("אפשרות לא חוקית בפקודה '{name}'", name=name))
            desc = str(item.get("description") or "").strip()[:100]
            hint = str(item.get("arg_hint") or "").strip()[:100]
            seen.add(name)
            out.append({"name": name, "response": response, "role_id": role_id, "role_action": role_action, "kind": kind,
                        "send_mode": send_mode, "delete_trigger": bool(item.get("delete_trigger")),
                        "description": desc, "arg_hint": hint})
        if len(out) > 100:
            raise ValueError(tr("עד 100 פקודות"))
        return out
    if t == "reaction_roles":
        return value or []
    if t == "automations":
        from .automations_schema import validate_automations
        return validate_automations(value)
    if t == "date":
        value = str(value or "").strip()
        if value:
            import datetime as _dt
            try:
                _dt.date.fromisoformat(value)
            except ValueError:
                raise ValueError(tr("{label}: תאריך בפורמט YYYY-MM-DD", label=label)) from None
        return value
    if t == "rule":
        value = value if isinstance(value, dict) else {}
        out = {"on": bool(value.get("on", field["default"]["on"]))}
        action = value.get("action", field["default"]["action"])
        if action not in [a[0] for a in field["actions"]]:
            raise ValueError(tr("{label}: פעולה לא חוקית", label=label))
        out["action"] = action
        for param in field.get("params", []):
            out[param["key"]] = _validate_field({**param, "_label": f"{label}: {tr(param['label'])}"},
                                                value.get(param["key"], param["default"]))
        return out
    if t == "list":
        items = value if isinstance(value, list) else []
        if len(items) > field.get("max_items", 50):
            raise ValueError(tr("{label}: עד {n} שורות", label=label, n=field.get('max_items', 50)))
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            missing = [f for f in field["item_fields"] if f.get("required") and not str(item.get(f["key"], "")).strip()]
            if missing:
                filled = any(str(item.get(f["key"], "")).strip() for f in field["item_fields"]
                             if f["type"] not in ("select", "bool", "number"))
                if not filled:
                    continue  # an empty row is just ignored
                raise ValueError(tr("{label}: בשורה {n} חסר '{field}'", label=label, n=len(out) + 1,
                                    field=tr(missing[0]["label"])))
            out.append({f["key"]: _validate_field({**f, "_label": f"{label}: {tr(f['label'])}"}, item.get(f["key"], f["default"]))
                        for f in field["item_fields"]})
        if field.get("unique"):
            seen = set()
            for row in out:
                key = str(row[field["unique"]]).lower()
                if key in seen:
                    raise ValueError(tr("{label}: '{value}' מופיע פעמיים", label=label, value=row[field['unique']]))
                seen.add(key)
        return out
    raise ValueError(tr("סוג שדה לא מוכר: {t}", t=t))


def validate_config(key: str, raw: dict, current: dict | None = None) -> dict:
    """Returns a clean config. Fields marked `managed` keep their current value."""
    current = current or module_defaults(key)
    clean = {}
    for field in MODULES_BY_KEY[key]["fields"]:
        fkey = field["key"]
        if field.get("managed"):
            clean[fkey] = current.get(fkey, field["default"])
            continue
        clean[fkey] = _validate_field(field, raw.get(fkey, current.get(fkey, field["default"])))
    if key == "economy" and clean["min_bet"] > clean["max_bet"]:
        raise ValueError(tr("ההימור המינימלי גדול מהמקסימלי"))
    return clean


# ---- catalog for the UI -------------------------------------------------------
def catalog_json() -> dict:
    return {
        "permissions": [
            {"key": k, "bit": str(b), "label": label, "dangerous": d} for k, b, label, d in PERMISSIONS
        ],
        "base_permissions": BASE_PERMS,
        "categories": [{"key": k, "label": l, "en": en} for k, l, en in CATEGORIES],
        "intent_labels": INTENT_LABELS,
        "modules": MODULES,
        "automations": __import__("botd.automations_schema", fromlist=["schema_json"]).schema_json(),
    }


def recommended_permissions(enabled_modules: set[str]) -> list[str]:
    perms = list(BASE_PERMS)
    for module in MODULES:
        if module["key"] in enabled_modules:
            perms.extend(p for p in module["permissions"] if p not in perms)
    return perms


def permissions_value(keys: list[str]) -> int:
    value = 0
    for key in keys:
        if key not in PERM_BITS:
            raise ValueError(tr("הרשאה לא מוכרת: {key}", key=key))
        value |= PERM_BITS[key]
    return value


def invite_url(client_id: str, perm_keys: list[str], guild_id: str = "") -> str:
    params = {
        "client_id": client_id,
        "permissions": str(permissions_value(perm_keys)),
        "scope": "bot applications.commands",
        "integration_type": "0",
    }
    if guild_id:
        params["guild_id"] = guild_id
        params["disable_guild_select"] = "true"
    return "https://discord.com/oauth2/authorize?" + urlencode(params)
