"""Keeps secrets out of logs.

Every secret the app loads is registered here, and every formatted log line
(including tracebacks) passes through `redact()` before it reaches a file,
the console or the UI. Token-shaped strings are masked even if never registered.
"""

import logging
import re
import threading

_lock = threading.Lock()
_secrets: set[str] = set()

_PATTERNS = [
    # Discord bot token: base64(id).timestamp.hmac
    re.compile(r"[A-Za-z\d_-]{23,28}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,}"),
    # Anthropic API key
    re.compile(r"sk-ant-[A-Za-z\d_-]{10,}"),
    # "Bot <token>" auth headers
    re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?bot\s+)[^\s'\"]+"),
]

MASK = "[REDACTED]"


def register_secret(value: str | None) -> None:
    if value and len(value) >= 8:
        with _lock:
            _secrets.add(value)


def unregister_secret(value: str | None) -> None:
    if value:
        with _lock:
            _secrets.discard(value)


def redact(text: str) -> str:
    if not text:
        return text
    with _lock:
        known = sorted(_secrets, key=len, reverse=True)
    for secret in known:
        if secret in text:
            text = text.replace(secret, MASK)
    for pattern in _PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + MASK, text)
        else:
            text = pattern.sub(MASK, text)
    return text


class RedactingFormatter(logging.Formatter):
    """A Formatter whose whole output (message + traceback) is redacted."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
