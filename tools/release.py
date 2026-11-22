"""Publish a new version to GitHub.

  python tools/release.py 1.0.1 "What changed in this version"

What it does:
1. sets botd/version.py to the new version,
2. moves the zip of every older version from dist/ into OldVersions/ (kept in the repository for manual downgrades),
3. builds dist/FreeDiscordBot-v<version>.zip from the files tracked by git,
4. commits, tags v<version>, pushes, and creates a GitHub Release with the zip attached.

The app's Updates page reads these Releases: the newest is offered as an update, older ones as "go back".
Needs: git, and the GitHub CLI (gh) logged in to your account.
"""

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST, OLD = ROOT / "dist", ROOT / "OldVersions"


def run(*cmd: str) -> str:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def main() -> int:
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        print(__doc__)
        return 1
    version, notes = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else f"Version {sys.argv[1]}")
    if run("git", "status", "--porcelain").strip():
        print("Commit or stash your changes first.")
        return 1

    vfile = ROOT / "botd" / "version.py"
    vfile.write_text(re.sub(r'VERSION = "[^"]*"', f'VERSION = "{version}"', vfile.read_text(encoding="utf-8")), encoding="utf-8")

    DIST.mkdir(exist_ok=True)
    OLD.mkdir(exist_ok=True)
    for old_zip in DIST.glob("FreeDiscordBot-v*.zip"):
        shutil.move(str(old_zip), OLD / old_zip.name)
        print("moved to OldVersions:", old_zip.name)

    run("git", "add", "-A")
    files = [f for f in run("git", "ls-files", "--cached").splitlines()
             if f and not f.startswith(("OldVersions/", "dist/", ".github/"))]
    target = DIST / f"FreeDiscordBot-v{version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(ROOT / f, f)
    print("built", target.name, f"({target.stat().st_size // 1024} KB, {len(files)} files)")

    run("git", "commit", "-m", f"Release v{version}")
    run("git", "tag", f"v{version}")
    run("git", "push", "--follow-tags")
    run("gh", "release", "create", f"v{version}", str(target), "--title", f"v{version}", "--notes", notes)
    print(f"Released v{version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
