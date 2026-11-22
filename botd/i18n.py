"""Translations. Source strings are Hebrew; each translation table maps Hebrew -> (English, Russian, Arabic).

- tr(text, lang, **kw): translate a source string and fill {placeholders}.
- The UI language is global (one local user); each Discord server has its own bot language.
"""

import copy

LANGS = {
    "he": {"name": "עברית", "dir": "rtl"},
    "en": {"name": "English", "dir": "ltr"},
    "ru": {"name": "Русский", "dir": "ltr"},
    "ar": {"name": "العربية", "dir": "rtl"},
}
_INDEX = {"en": 0, "ru": 1, "ar": 2}

_table: dict[str, tuple[str, str, str]] | None = None
_ui_lang = "he"


def table() -> dict[str, tuple[str, str, str]]:
    global _table
    if _table is None:
        from .locale_data import bot, catalog, features, server, ui
        merged: dict = {}
        for part in (features.S, catalog.S, server.S, bot.S, ui.S):  # older tables win on shared keys
            merged.update(part)
        _table = merged
    return _table


def valid_lang(lang: str) -> bool:
    return lang in LANGS


def set_ui_lang(lang: str) -> None:
    global _ui_lang
    _ui_lang = lang if lang in LANGS else "he"


def ui_lang() -> str:
    return _ui_lang


def tr(text: str, lang: str | None = None, **kw) -> str:
    lang = lang or _ui_lang
    out = text
    if lang != "he" and text:
        row = table().get(text)
        if row:
            out = row[_INDEX[lang]] or text
    if kw:
        try:
            out = out.format(**kw)
        except (KeyError, IndexError, ValueError):
            out = text.format(**kw)
    return out


def N_(text: str) -> str:
    """Marks a source string for translation without translating it now (slash descriptions, constants)."""
    return text


def ui_strings(lang: str) -> dict[str, str]:
    """Hebrew -> translation for every known string (sent to the UI)."""
    if lang == "he":
        return {}
    i = _INDEX[lang]
    return {k: v[i] for k, v in table().items() if v[i]}


# ---- catalog translation ---------------------------------------------------------
_TEXT_KEYS = ("label", "description", "hint")


def translate_catalog(data: dict, lang: str) -> dict:
    if lang == "he":
        return data
    data = copy.deepcopy(data)

    def walk_field(f: dict) -> None:
        for k in _TEXT_KEYS:
            if f.get(k):
                f[k] = tr(f[k], lang)
        for opt in f.get("options", []) or f.get("actions", []) or []:
            opt[1] = tr(opt[1], lang)
        for sub in f.get("params", []) + f.get("item_fields", []):
            walk_field(sub)
        if isinstance(f.get("default"), str) and f.get("type") in ("text", "textarea"):
            f["default"] = tr(f["default"], lang)

    for p in data["permissions"]:
        p["label"] = tr(p["label"], lang)
    for c in data["categories"]:
        c["label"] = tr(c["label"], lang)
    for m in data["modules"]:
        m["label"] = tr(m["label"], lang)
        m["description"] = tr(m["description"], lang)
        for f in m["fields"]:
            walk_field(f)
    auto = data.get("automations", {})
    for group in ("triggers", "targets"):
        auto[group] = {k: tr(v, lang) for k, v in auto.get(group, {}).items()}
    for group in ("conditions", "actions"):
        for item in auto.get(group, {}).values():
            item["label"] = tr(item["label"], lang)
    return data
