"""Customization: every bot message, embed color and emoji per server; slash command names; app look.

Bot messages are found by scanning the module code for tr(text, lang, ...) calls (the ones that go to Discord) and
N_() texts that are shown to members later, so new messages become customizable automatically.
"""

import ast
import re
from functools import lru_cache
from pathlib import Path

from .i18n import N_, tr

MODULES_DIR = Path(__file__).resolve().parent / "modules"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
# module file -> group name shown in the app
GROUP_NAMES = {
    "afk": N_("סטטוס AFK"), "automations": N_("אוטומציות"), "automod": N_("אוטומוד"), "autorole": N_("תפקיד אוטומטי"),
    "birthdays": N_("ימי הולדת"), "channels_auto": N_("ערוצים"), "claude_actions": N_("צ'אט AI"), "claude_chat": N_("צ'אט AI"),
    "common": N_("כללי"), "custom_commands": N_("פקודות מותאמות"), "economy": N_("כלכלה"), "giveaways": N_("הגרלות"),
    "invite_tracker": N_("מעקב הזמנות"), "levels": N_("רמות"), "logs": N_("לוג פעולות"), "moderation": N_("פקודות ניהול"),
    "polls": N_("סקרים"), "reaction_roles": N_("תפקידים לפי ריאקשן"), "reminders": N_("תזכורות"), "restricted": N_("ערוצים מוגבלים"),
    "social_alerts": N_("התראות רשתות"), "starboard": "Starboard", "suggestions": N_("תיבת הצעות"), "ticketing": N_("כרטיסי תמיכה"),
    "warn_system": N_("אזהרות"), "welcome": N_("ברוכים הבאים"), "embeds": N_("הודעות Embed"),
}


def _is_command_decorator(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
        "command", "describe", "choices", "Group", "rename")


@lru_cache(maxsize=1)
def bot_texts() -> tuple[dict, ...]:
    """Every customizable bot message: [{key, group}], in code order, without duplicates."""
    seen: dict[str, str] = {}
    for path in sorted(MODULES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip: set[int] = set()
        for node in ast.walk(tree):  # texts of slash commands are customized on the Commands page instead
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    skip.update(id(n) for n in ast.walk(dec))
            if _is_command_decorator(node):
                skip.update(id(n) for n in ast.walk(node))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in skip or not node.args:
                continue
            name = getattr(node.func, "id", None)
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            if (name == "tr" and len(node.args) >= 2) or name == "N_":
                seen.setdefault(first.value, path.stem)
    return tuple({"key": k, "group": GROUP_NAMES.get(g, g)} for k, g in seen.items())


def text_catalog(lang: str) -> list[dict]:
    """The customizable messages, each with its default wording in the server's language."""
    from .i18n import Lang

    plain = Lang(str(lang))  # without the server's own texts, to show the default
    return [{"key": t["key"], "group": tr(t["group"]), "default": tr(t["key"], plain),
             "placeholders": sorted(set(PLACEHOLDER.findall(t["key"])))} for t in bot_texts()]


def validate_custom(raw: dict) -> dict:
    from .modules.common import EMOJIS, PALETTE

    keys = {t["key"] for t in bot_texts()}
    texts = {}
    for key, value in (raw.get("texts") or {}).items():
        value = str(value or "")
        if not value.strip():
            continue
        if key not in keys:
            raise ValueError(tr("טקסט לא מוכר: {key}", key=key[:60]))
        if len(value) > 1800:
            raise ValueError(tr("טקסט ארוך מדי (עד 1800 תווים)"))
        missing = set(PLACEHOLDER.findall(key)) - set(PLACEHOLDER.findall(value))
        extra = set(PLACEHOLDER.findall(value)) - set(PLACEHOLDER.findall(key))
        if extra:
            raise ValueError(tr("בטקסט '{key}' יש משתנה שלא קיים: {names}", key=key[:40],
                                names=", ".join("{" + n + "}" for n in sorted(extra))))
        _ = missing  # leaving a placeholder out is fine (for example a shorter message)
        texts[key] = value
    colors = {}
    for name, value in (raw.get("colors") or {}).items():
        value = str(value or "").strip()
        if not value:
            continue
        if name not in PALETTE or not HEX.match(value):
            raise ValueError(tr("צבע לא תקין: {name}", name=name))
        colors[name] = value
    emojis = {}
    for name, value in (raw.get("emojis") or {}).items():
        value = str(value or "").strip()
        if not value:
            continue
        if name not in EMOJIS or len(value) > 64:
            raise ValueError(tr("אימוג'י לא תקין: {name}", name=name))
        emojis[name] = value
    return {"texts": texts, "colors": colors, "emojis": emojis}


# ---- slash commands (global: Discord shows the same commands in every server) ----
CMD_NAME = re.compile(r"^[-_\w]{1,32}$", re.UNICODE)


def validate_commands(raw: dict, known: set[str]) -> dict:
    out, names = {}, {}
    for cmd, opts in (raw or {}).items():
        if cmd not in known or not isinstance(opts, dict):
            continue
        name = str(opts.get("name") or "").strip().lower()
        desc = str(opts.get("description") or "").strip()
        enabled = bool(opts.get("enabled", True))
        if name and (not CMD_NAME.match(name) or name != name.lower()):
            raise ValueError(tr("שם פקודה לא תקין: '{name}' (אותיות קטנות, מספרים, _ או -, עד 32 תווים)", name=name))
        if len(desc) > 100:
            raise ValueError(tr("תיאור ארוך מדי בפקודה /{name} (עד 100 תווים)", name=cmd))
        final = name or cmd
        if enabled:
            if final in names:
                raise ValueError(tr("שני פקודות באותו שם: /{name}", name=final))
            names[final] = cmd
        entry = {}
        if name and name != cmd:
            entry["name"] = name
        if desc:
            entry["description"] = desc
        if not enabled:
            entry["enabled"] = False
        if entry:
            out[cmd] = entry
    for cmd in known - set(out):  # a renamed command must not take the name of an untouched one
        if cmd in names and names[cmd] != cmd:
            raise ValueError(tr("שני פקודות באותו שם: /{name}", name=cmd))
    return out


# ---- app look ----
APPEARANCE_DEFAULTS = {"theme": "dark", "accent": "#6D5DFC", "background": "", "surface": "", "text": "",
                       "font_scale": 100, "radius": 12, "compact": False, "custom_css": ""}


def validate_appearance(raw: dict) -> dict:
    out = dict(APPEARANCE_DEFAULTS)
    for key, value in (raw or {}).items():
        if key not in out:
            continue
        if key == "theme":
            if value not in ("dark", "light"):
                raise ValueError("theme")
            out[key] = value
        elif key in ("accent", "background", "surface", "text"):
            value = str(value or "").strip()
            if value and not HEX.match(value):
                raise ValueError(tr("צבע לא תקין: {name}", name=key))
            out[key] = value or APPEARANCE_DEFAULTS[key]
        elif key == "font_scale":
            out[key] = max(80, min(int(value or 100), 140))
        elif key == "radius":
            out[key] = max(0, min(int(value or 12), 24))
        elif key == "compact":
            out[key] = bool(value)
        elif key == "custom_css":
            value = str(value or "")
            if len(value) > 20000:
                raise ValueError(tr("ה-CSS ארוך מדי"))
            if re.search(r"</?\s*style|@import|url\s*\(\s*['\"]?\s*(https?:|//)", value, re.I):
                raise ValueError(tr("ה-CSS לא יכול לטעון קבצים מבחוץ"))
            out[key] = value
    return out
