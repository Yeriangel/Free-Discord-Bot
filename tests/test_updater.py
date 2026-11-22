"""Updater tests: version ordering, package checks (zip-slip), install + backup into a temporary app folder."""
import asyncio
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BOTD_DATA_DIR"] = tempfile.mkdtemp()

from botd import updater  # noqa: E402


def make_zip(files: dict, top: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(top + name, data)
    return buf.getvalue()


GOOD = {"app.py": "print('new')", "botd/__init__.py": "", "ui/app.js": "//new", "requirements.txt": "discord.py\n",
        ".venv/evil.txt": "must be skipped"}


def test_versions_and_hosts():
    assert updater.parse_version("v1.2.10") > updater.parse_version("v1.2.9") > updater.parse_version("1.1")
    assert updater._host_ok("https://github.com/x/y/releases/download/v1/a.zip")
    assert not updater._host_ok("http://github.com/x") and not updater._host_ok("https://evil.com/a.zip")
    assert updater.REPO_RE.match("yeri/Free-Discord-Bot") and not updater.REPO_RE.match("../etc")
    print("ok versions, allowed hosts, repo names")


def test_package_checks():
    zf = zipfile.ZipFile(io.BytesIO(make_zip(GOOD)))
    names = {m.filename for m in updater._check_package(zf)}
    assert "app.py" in names and not any(n.startswith(".venv") for n in names)
    zf = zipfile.ZipFile(io.BytesIO(make_zip(GOOD, top="Free-Discord-Bot-1.0.1/")))
    assert "botd/__init__.py" in {m.filename for m in updater._check_package(zf)}  # one top folder is fine
    for bad in ({**GOOD, "../outside.txt": "x"}, {"readme.txt": "not the app"}):
        try:
            updater._check_package(zipfile.ZipFile(io.BytesIO(make_zip(bad))))
            raise AssertionError(bad)
        except ValueError:
            pass
    print("ok package checks (zip-slip and wrong packages rejected)")


async def install_scenario():
    app = Path(tempfile.mkdtemp())
    (app / "app.py").write_text("print('old')")
    (app / "botd").mkdir()
    (app / "botd/__init__.py").write_text("")
    (app / "ui").mkdir()
    (app / "ui/app.js").write_text("//old")
    (app / "requirements.txt").write_text("discord.py\n")
    (app / ".venv").mkdir()
    (app / ".venv/keep.txt").write_text("venv")
    updater.APP_DIR = app
    data = make_zip(GOOD)

    async def fake_list(repo):
        return [{"tag": "v1.0.1", "zip_url": "https://github.com/o/r/releases/download/v1.0.1/a.zip", "size": len(data)}]

    class FakeResp:
        status_code, content, url = 200, data, "https://objects.githubusercontent.com/a.zip"

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return FakeResp()

    updater.list_releases = fake_list
    updater.httpx.AsyncClient = FakeClient
    result = await updater.install("o/r", "v1.0.1")
    assert (app / "app.py").read_text() == "print('new')" and (app / "ui/app.js").read_text() == "//new"
    assert (app / ".venv/keep.txt").read_text() == "venv" and not (app / ".venv/evil.txt").exists()
    backup = Path(result["backup"])
    assert (backup / "app.py").read_text() == "print('old')" and not (backup / ".venv").exists()
    assert result["restart_required"]
    print("ok install: files replaced, .venv untouched, backup of the old version saved")


if __name__ == "__main__":
    test_versions_and_hosts()
    test_package_checks()
    asyncio.run(install_scenario())
