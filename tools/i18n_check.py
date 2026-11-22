"""Translation coverage check.

Finds every translatable source string (catalog texts, tr("...")/N_("...") in Python, t("...") in the UI)
and reports strings that have no English/Russian/Arabic translation, and Hebrew text left outside those calls.

  python tools/i18n_check.py            # summary + missing strings
  python tools/i18n_check.py --strict   # exit code 1 if anything is missing
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HEB = re.compile(r"[֐-׿]")
JS_CALL = re.compile(r"""\bt\(\s*(["'`])((?:\\.|(?!\1).)*)\1""", re.S)
HTML_ATTR = re.compile(r'data-i18n(?:-[a-z]+)?="([^"]+)"')
PY_FUNCS = {"tr", "N_", "fail", "lbl"}


def catalog_strings() -> set[str]:
    from botd import catalog
    data = catalog.catalog_json()
    out: set[str] = set()

    def field(f):
        for k in ("label", "description", "hint"):
            if f.get(k):
                out.add(f[k])
        for opt in f.get("options", []) or f.get("actions", []) or []:
            out.add(opt[1])
        for sub in f.get("params", []) + f.get("item_fields", []):
            field(sub)
        if isinstance(f.get("default"), str) and f.get("type") in ("text", "textarea") and HEB.search(f["default"]):
            out.add(f["default"])

    out.update(p["label"] for p in data["permissions"])
    out.update(c["label"] for c in data["categories"])
    for m in data["modules"]:
        out.update((m["label"], m["description"]))
        for f in m["fields"]:
            field(f)
    for group in ("triggers", "targets"):
        out.update(data["automations"][group].values())
    for group in ("conditions", "actions"):
        out.update(v["label"] for v in data["automations"][group].values())
    return {s for s in out if HEB.search(s)}


def python_strings() -> tuple[set[str], list[str]]:
    found, stray = set(), []
    for path in (ROOT / "botd").rglob("*.py"):
        if "locale_data" in path.parts or path.name in ("catalog_modules.py", "i18n.py"):
            continue
        source = path.read_text(encoding="utf-8")
        ignored = {i for i, line in enumerate(source.splitlines(), 1) if "i18n: ignore" in line}
        tree = ast.parse(source)
        marked: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in PY_FUNCS:
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    found.add(node.args[0].value)
                    marked.add(id(node.args[0]))
        for node in ast.walk(tree):
            if getattr(node, "lineno", 0) in ignored:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and HEB.search(node.value) \
                    and id(node) not in marked and not path.name.startswith("catalog"):
                stray.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value[:60]!r}")
            if isinstance(node, ast.JoinedStr) and any(isinstance(v, ast.Constant) and HEB.search(str(v.value)) for v in node.values):
                stray.append(f"{path.relative_to(ROOT)}:{node.lineno}: f-string with Hebrew")
    return found, stray


def ui_strings() -> tuple[set[str], list[str]]:
    found, stray = set(), []
    js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    for m in JS_CALL.finditer(js):
        if HEB.search(m.group(2)):
            found.add(m.group(2).encode().decode("unicode_escape").encode("latin1").decode("utf-8") if "\\u" in m.group(2) else m.group(2))
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    found.update(s for s in HTML_ATTR.findall(html) if HEB.search(s))
    covered = {m.span() for m in JS_CALL.finditer(js)}
    for lineno, line in enumerate(js.splitlines(), 1):
        if HEB.search(line):
            stripped = JS_CALL.sub("", line)
            if HEB.search(stripped) and "/*" not in line and not line.strip().startswith("//"):
                stray.append(f"ui/app.js:{lineno}: {line.strip()[:70]}")
    for lineno, line in enumerate(html.splitlines(), 1):
        if HEB.search(HTML_ATTR.sub("", line)) and "data-i18n" not in line:
            stray.append(f"ui/index.html:{lineno}: {line.strip()[:70]}")
    return found, stray


def main() -> int:
    from botd import i18n
    tbl = i18n.table()
    py, py_stray = python_strings()
    ui, ui_stray = ui_strings()
    cat = catalog_strings()
    needed = {"catalog": cat, "python": py, "ui": ui}
    missing = {k: sorted(s for s in v if s not in tbl or not all(tbl[s])) for k, v in needed.items()}
    print(json.dumps({k: len(v) for k, v in needed.items()}), "strings;",
          json.dumps({k: len(v) for k, v in missing.items()}), "missing")
    for k, v in missing.items():
        for s in v:
            print(f"MISSING [{k}] {s!r}")
    for s in py_stray + ui_stray:
        print("UNWRAPPED", s)
    bad = sum(len(v) for v in missing.values()) + len(py_stray) + len(ui_stray)
    return 1 if bad and "--strict" in sys.argv else 0


if __name__ == "__main__":
    sys.exit(main())
