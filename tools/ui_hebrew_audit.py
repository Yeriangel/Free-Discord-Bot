"""Finds Hebrew texts in the UI code (ui/app.js) and in catalog defaults that have no translation.

The app translates its pages on the fly, so any Hebrew string literal in app.js must exist in the translation
table, either exactly or as a pattern with {placeholders}. This also catches labels in arrays and objects
(dropdown options, emoji names) that the main checker cannot see because they are not inside t("...").

  python tools/ui_hebrew_audit.py           # list missing texts
  python tools/ui_hebrew_audit.py --strict  # exit code 1 if anything is missing
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HEB = re.compile(r"[֐-׿]")
STR = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`((?:[^`\\]|\\.)*)`', re.S)
TAGS = re.compile(r"<[^>]+>")


def pattern_match(text: str, patterns: list) -> bool:
    return any(p.fullmatch(text) for p in patterns)


def build_patterns(table: dict) -> list:
    out = []
    for key in table:
        if re.search(r"\{\w+\}", key):
            body = "".join("(.+?)" if re.fullmatch(r"\{\w+\}", part) else re.escape(part)
                           for part in re.split(r"(\{\w+\})", key))
            out.append(re.compile(body, re.S))
    return out


def candidates(js: str) -> set[str]:
    """Hebrew pieces of string literals, split at HTML tags and ${...} like the page text nodes would be."""
    found = set()
    for m in STR.finditer(js):
        s = next(g for g in m.groups() if g is not None)
        if not HEB.search(s):
            continue
        s = s.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")
        # a ${value} inside a sentence ends up as text on the page: treat it like a {placeholder}
        s = re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", "{x}", s)
        for attr in re.findall(r'(?:placeholder|title)="([^"]*)"', s):
            if HEB.search(attr) and "{x}" not in attr:
                found.add(attr.strip())
        s = re.sub(r'\w+="[^"]*"', "", s)
        for part in TAGS.split(s):
            part = re.sub(r'^[\s"\'>:,)}]+|[\s"\'<:,({]+$', "", part.strip())
            while part and part[-1] in ")}\"'" and (part.count(")") > part.count("(") or part.count("}") > part.count("{")
                                                     or part[-1] in "\"'"):
                part = part[:-1].rstrip()  # code left over after the string, like the end of a function call
            if HEB.search(part) and part not in ("{x}",):
                found.add(part)
    return found


def catalog_defaults() -> set[str]:
    from botd import catalog
    out = set()
    for m in catalog.MODULES:
        for f in m["fields"]:
            if f["type"] == "list" and isinstance(f.get("default"), list):
                for row in f["default"]:
                    out.update(v for v in row.values() if isinstance(v, str) and HEB.search(v))
    return out


def main() -> int:
    from botd import i18n
    table = i18n.table()
    patterns = build_patterns(table)
    js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    missing = sorted(t for t in candidates(js) | catalog_defaults()
                     if t not in table and not pattern_match(t, patterns))
    for t in missing:
        print("MISSING", repr(t))
    print(f"{len(missing)} untranslated UI texts")
    return 1 if missing and "--strict" in sys.argv else 0


if __name__ == "__main__":
    sys.exit(main())
