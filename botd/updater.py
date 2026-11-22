"""Online updates from GitHub Releases, with manual rollback to any older release.

Each release carries a zip asset (FreeDiscordBot-vX.Y.Z.zip) built by tools/release.py. Installing a version:
1. downloads the zip from GitHub (https only, GitHub hosts only),
2. checks it is a real app package (botd/ + ui/ + app.py) with no paths escaping the folder,
3. backs up the current files to %APPDATA%\\DiscordBotManager\\backups\\<version>,
4. copies the new files over the app folder (never touching .venv, .git or user data),
5. installs requirements if they changed. The app must then be restarted.
"""

import hashlib
import io
import logging
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

from . import paths
from .i18n import tr
from .version import UPDATE_REPO, VERSION

log = logging.getLogger("botd.updater")

APP_DIR = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
# GitHub serves release files from these hosts (it moved downloads to release-assets.* in 2025-2026)
ALLOWED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com", "codeload.github.com",
                 "release-assets.githubusercontent.com")
SKIP_DIRS = {".venv", ".git", "__pycache__", "OldVersions", "dist"}
REPO_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
MAX_ZIP = 50 * 1024 * 1024


def parse_version(tag: str) -> tuple:
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def _host_ok(url: str) -> bool:
    m = re.match(r"^https://([^/]+)/", url or "")
    return bool(m) and m.group(1).lower() in ALLOWED_HOSTS


def repo_for(store) -> str:
    repo = (store.get_kv("update_repo") or UPDATE_REPO or "").strip()
    return repo if REPO_RE.match(repo) else ""


async def list_releases(repo: str) -> list[dict]:
    if not REPO_RE.match(repo):
        raise ValueError(tr("לא הוגדר מאגר GitHub לעדכונים"))
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "FreeDiscordBot-Updater"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(f"{API}/repos/{repo}/releases", params={"per_page": 50}, headers=headers)
    except httpx.HTTPError:
        raise ValueError(tr("אין חיבור ל-GitHub")) from None
    if resp.status_code == 404:
        raise ValueError(tr("המאגר {repo} לא נמצא ב-GitHub, או שהוא פרטי", repo=repo))
    if resp.status_code >= 400:
        raise ValueError(tr("GitHub החזיר שגיאה {status}", status=resp.status_code))
    out = []
    for r in resp.json():
        if r.get("draft"):
            continue
        asset = next((a for a in r.get("assets", []) if a.get("name", "").lower().endswith(".zip")), None)
        out.append({
            "tag": r.get("tag_name", ""), "name": r.get("name") or r.get("tag_name", ""),
            "published": r.get("published_at", ""), "notes": (r.get("body") or "")[:4000],
            "prerelease": bool(r.get("prerelease")),
            "zip_url": asset.get("browser_download_url") if asset else "",
            "size": asset.get("size", 0) if asset else 0,
        })
    out.sort(key=lambda r: parse_version(r["tag"]), reverse=True)
    current = parse_version(VERSION)
    for r in out:
        v = parse_version(r["tag"])
        r["is_current"] = v == current
        r["is_newer"] = v > current
    return out


def _check_package(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    names = zf.namelist()
    prefix = ""
    if names and all(n.startswith(names[0].split("/")[0] + "/") for n in names) and not names[0].startswith("botd/"):
        prefix = names[0].split("/")[0] + "/"  # zip with one top folder
    members = []
    for info in zf.infolist():
        rel = info.filename[len(prefix):] if info.filename.startswith(prefix) else info.filename
        if not rel or rel.endswith("/"):
            continue
        parts = Path(rel).parts
        if rel.startswith(("/", "\\")) or ".." in parts or ":" in rel or parts[0] in SKIP_DIRS:
            if parts and parts[0] in SKIP_DIRS:
                continue
            raise ValueError(tr("קובץ העדכון לא תקין"))
        info.filename = rel
        members.append(info)
    have = {m.filename for m in members}
    if not {"app.py", "botd/__init__.py", "ui/app.js"} <= have:
        raise ValueError(tr("קובץ העדכון לא נראה כמו גרסה של האפליקציה"))
    return members


def _backup_current() -> Path:
    dest = paths.data_dir() / "backups" / VERSION
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for item in APP_DIR.iterdir():
        if item.name in SKIP_DIRS:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, target)
    return dest


async def install(repo: str, tag: str) -> dict:
    releases = await list_releases(repo)
    rel = next((r for r in releases if r["tag"] == tag), None)
    if rel is None:
        raise ValueError(tr("הגרסה {tag} לא נמצאה", tag=tag))
    if not rel["zip_url"] or not _host_ok(rel["zip_url"]):
        raise ValueError(tr("לגרסה הזו אין קובץ התקנה"))
    if rel["size"] and rel["size"] > MAX_ZIP:
        raise ValueError(tr("קובץ העדכון גדול מדי"))
    log.info(tr("מוריד את גרסה %s…"), tag)
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True,
                                     headers={"User-Agent": "FreeDiscordBot-Updater"}) as client:
            resp = await client.get(rel["zip_url"])
    except httpx.HTTPError:
        raise ValueError(tr("אין חיבור ל-GitHub")) from None
    if resp.status_code != 200 or len(resp.content) > MAX_ZIP or not _host_ok(str(resp.url)):
        log.warning("update download rejected: HTTP %s, %d bytes, from %s", resp.status_code, len(resp.content),
                    httpx.URL(str(resp.url)).host)
        raise ValueError(tr("ההורדה נכשלה"))
    data = resp.content
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError(tr("קובץ העדכון לא תקין")) from None
    members = _check_package(zf)
    old_req = (APP_DIR / "requirements.txt").read_bytes() if (APP_DIR / "requirements.txt").exists() else b""
    backup = _backup_current()
    log.info(tr("גיבוי של הגרסה הנוכחית נשמר ב-%s"), backup)
    for info in members:
        target = (APP_DIR / info.filename).resolve()
        if APP_DIR not in target.parents:
            raise ValueError(tr("קובץ העדכון לא תקין"))
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    new_req = (APP_DIR / "requirements.txt").read_bytes() if (APP_DIR / "requirements.txt").exists() else b""
    if new_req and new_req != old_req:
        log.info(tr("מתקין חבילות שהשתנו…"))
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(APP_DIR / "requirements.txt")],
                       check=False, timeout=600)
    log.info(tr("גרסה %s הותקנה. צריך להפעיל את האפליקציה מחדש."), tag)
    return {"installed": tag, "backup": str(backup), "sha256": hashlib.sha256(data).hexdigest(), "restart_required": True}
