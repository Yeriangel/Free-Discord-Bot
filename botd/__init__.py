"""botd - the bot service: Discord bot + local control API. No UI code here."""

import hashlib
from pathlib import Path

from .version import VERSION as __version__  # noqa: E402,F401

_ROOT = Path(__file__).resolve().parent.parent


def build_id() -> str:
    """Fingerprint of the app's code files (names, sizes, change times). Changes whenever the code on disk changes,
    so the launcher can tell that a running service is older than the files, even without a new version number."""
    h = hashlib.sha256()
    for pattern in ("botd/**/*.py", "ui/*", "app.py"):
        for f in sorted(_ROOT.glob(pattern)):
            if f.is_file() and "__pycache__" not in f.parts:
                st = f.stat()
                h.update(f"{f.relative_to(_ROOT)}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]
