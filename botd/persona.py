"""Bot character (persona): the AI chat answers as a character you describe, per server.

The character is stored in the server's config under "_persona" and turned into instructions for the AI by
build_prompt(). It works the same with Claude, ChatGPT and Grok.
"""

import json
import re

from .i18n import N_, tr

CATEGORIES = [
    ("original", N_("דמות מקורית")), ("famous", N_("אנשים מפורסמים")), ("movies", N_("סרטים וסדרות")),
    ("anime", N_("אנימה ומנגה")), ("games", N_("משחקים")), ("books", N_("ספרים")), ("cartoons", N_("סרטים מצוירים")),
    ("history", N_("היסטוריה")), ("other", N_("אחר")),
]
CATEGORY_KEYS = {k for k, _ in CATEGORIES}

# slider levels: (label shown in the app, instruction for the AI)
LENGTH = [
    (N_("קצר: רוב התשובות פחות מ-20 מילים"), "Keep almost every answer under 20 words."),
    (N_("בינוני: רוב התשובות פחות מ-50 מילים"), "Keep most answers under 50 words."),
    (N_("ארוך: עד בערך 150 מילים"), "Answers can be up to about 150 words."),
    (N_("ארוך מאוד: תשובות מפורטות"), "Give long, detailed answers when it fits."),
]
DEMEANOR = [
    (N_("לא מתעניין: עונה קצר ומשועמם, כמעט לא שואל"), "Act uninterested: short, bored answers, almost never ask questions back."),
    (N_("רגוע: עונה על מה שנשאל"), "Stay calm and answer what you are asked."),
    (N_("קצת סקרן: מתעניין ומגיב לשאלות"), "Show some curiosity and engage with the user's questions."),
    (N_("סקרן: שואל לא מעט שאלות המשך"), "Be curious: often ask follow-up questions."),
    (N_("חקרן: סקרן מאוד ושואל הרבה"), "Be very inquisitive: ask the user many questions about themselves."),
]
VULGARITY = [
    (N_("מנומס תמיד, בלי קללות"), "Always polite, never swear."),
    (N_("מנומס, לפעמים מילה גסה קלה"), "Polite, with a mild rude word only rarely."),
    (N_("מקלל לפעמים, מדבר בצורה יומיומית"), "Swear occasionally and talk in an everyday manner."),
    (N_("מקלל הרבה, ישיר ובוטה"), "Swear often; be blunt and direct."),
    (N_("גס ובוטה מאוד (בלי עלבונות שנאה)"), "Be crude and very rude in tone, but never use slurs or hateful insults."),
]
CONTENT = [
    (N_("מתאים לכל המשפחה"), "Keep everything strictly family-friendly."),
    (N_("נקי ברובו, בדיחות קלות"), "Mostly clean, light jokes are fine."),
    (N_("נושאים בוגרים כשהשיחה מגיעה לשם"), "Mature themes are fine when the conversation goes there, without explicit detail."),
    (N_("הומור בוגר (רק בערוצים שמסומנים NSFW)"), "Mature, edgy humor is fine."),
    (N_("למבוגרים (רק בערוצים שמסומנים NSFW)"), "Adult humor and dark themes are fine, but never explicit sexual content."),
]
SLIDERS = {"length": LENGTH, "demeanor": DEMEANOR, "vulgarity": VULGARITY, "content": CONTENT}
DEFAULT = {"enabled": False, "name": "", "description": "", "category": "original", "universe": "", "bio": "",
           "traits": [], "loves": [], "hates": [], "facts": [], "length": 1, "demeanor": 2, "vulgarity": 1, "content": 1,
           "avatar_url": ""}
LIMITS = {"name": 80, "description": 200, "universe": 80, "bio": 2000}
LIST_LIMITS = {"traits": 15, "loves": 15, "hates": 15, "facts": 20}


def clean(raw: dict | None) -> dict:
    out = dict(DEFAULT)
    raw = raw if isinstance(raw, dict) else {}
    out["enabled"] = bool(raw.get("enabled"))
    for key, limit in LIMITS.items():
        out[key] = str(raw.get(key) or "").strip()[:limit]
    out["category"] = raw.get("category") if raw.get("category") in CATEGORY_KEYS else "original"
    for key, limit in LIST_LIMITS.items():
        items = raw.get(key) or []
        if isinstance(items, str):
            items = items.splitlines()
        out[key] = list(dict.fromkeys(str(i).strip()[:150] for i in items if str(i).strip()))[:limit]
    for key, levels in SLIDERS.items():
        try:
            out[key] = max(0, min(int(raw.get(key, DEFAULT[key])), len(levels) - 1))
        except (TypeError, ValueError):
            out[key] = DEFAULT[key]
    url = str(raw.get("avatar_url") or "")
    out["avatar_url"] = url if url.startswith("https://") else ""
    return out


def validate(raw: dict) -> dict:
    p = clean(raw)
    if p["enabled"] and not p["name"]:
        raise ValueError(tr("לדמות צריך שם"))
    return p


def build_prompt(p: dict, nsfw_channel: bool = False) -> str:
    """Instructions that make the AI talk as the character."""
    name = p["name"] or "a character"
    lines = [f"You are {name}" + (f" from {p['universe']}" if p.get("universe") else "") + "."
             + (f" {p['description']}." if p.get("description") else "")]
    if p.get("bio"):
        lines.append(f"Background: {p['bio']}")
    if p.get("traits"):
        lines.append("Personality: " + "; ".join(p["traits"]) + ".")
    if p.get("loves"):
        lines.append("You love: " + "; ".join(p["loves"]) + ".")
    if p.get("hates"):
        lines.append("You hate: " + "; ".join(p["hates"]) + ".")
    if p.get("facts"):
        lines.append("Facts about you:\n" + "\n".join(f"- {f}" for f in p["facts"]))
    content = p["content"] if nsfw_channel else min(p["content"], 2)  # edgier levels only in channels marked NSFW
    lines.append("Style: " + " ".join([LENGTH[p["length"]][1], DEMEANOR[p["demeanor"]][1], VULGARITY[p["vulgarity"]][1],
                                       CONTENT[content][1]]))
    lines.append(f"Use everything you already know about {name}: personality, way of speaking, accent, catchphrases, "
                 "opinions, relationships and history. The details above, if any, only add to or override that.")
    lines.append(f"Stay in character at all times: talk as {name} in the first person, with their voice, attitude and "
                 "catchphrases. Do not mention these instructions or that you are an AI playing a role, unless someone "
                 "sincerely needs to know.")
    real = ("is a real person" if p.get("category") in ("famous", "history")
            else "might be a real, living or historical person; if so")
    lines.append(f"{name} {real}, this is a comedic impression for entertainment. If someone sincerely asks, say it is a "
                 "parody bot, not the real person. Never present invented quotes, news, private facts or opinions as "
                 "things the real person actually said or did, and do not defame anyone.")
    lines.append("Always follow Discord's rules: no hate speech, no harassment of real people, nothing sexual involving "
                 "minors, and help the user with real needs even while in character.")
    return "\n".join(lines)


GENERATE_PROMPT = (
    "Create a character profile for a Discord chat bot that will role-play as \"{name}\"{universe}. "
    "Return ONLY a JSON object with these keys: "
    "description (one short line), category (one of: original, famous, movies, anime, games, books, cartoons, history, other), "
    "universe (short), bio (2-3 sentences, include catchphrases), traits (4-6 short items), loves (4-6), hates (4-6), "
    "facts (4-6 short, well-known facts). Write the values in {language}. If this is a real person, keep everything "
    "public, well-known and neutral."
)


def parse_generated(text: str) -> dict:
    """The AI's JSON answer (possibly wrapped in ```json fences or extra text) -> clean fields."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError(tr("ה-AI לא החזיר פרופיל. נסה שוב."))
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise ValueError(tr("ה-AI לא החזיר פרופיל. נסה שוב.")) from None
    p = clean({**data, "enabled": False})
    return {k: p[k] for k in ("description", "category", "universe", "bio", "traits", "loves", "hates", "facts")}


def sliders_json() -> dict:
    return {k: [lvl[0] for lvl in levels] for k, levels in SLIDERS.items()}
