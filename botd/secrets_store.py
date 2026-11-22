"""Secrets live in Windows Credential Manager (via keyring), never in files or the DB."""

from .i18n import tr
import os

import keyring
from keyring.errors import PasswordDeleteError

from .redact import register_secret, unregister_secret

SERVICE = os.environ.get("BOTD_KEYRING_SERVICE", "DiscordBotManager")
NAMES = ("bot_token", "anthropic_key", "openai_key", "xai_key", "twitch_client_id", "twitch_client_secret")


def _check(name: str) -> None:
    import re

    if name not in NAMES and not re.fullmatch(r"bot_token_[0-9a-f]{6}", name):
        raise KeyError(name)


def get(name: str) -> str | None:
    _check(name)
    value = keyring.get_password(SERVICE, name)
    register_secret(value)
    return value or None


def set(name: str, value: str) -> None:  # noqa: A001 - module-level API
    _check(name)
    value = value.strip()
    if not value:
        raise ValueError(tr("ערך ריק"))
    old = keyring.get_password(SERVICE, name)
    register_secret(value)
    keyring.set_password(SERVICE, name, value)
    if old and old != value:
        unregister_secret(old)


def delete(name: str) -> None:
    _check(name)
    old = keyring.get_password(SERVICE, name)
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass
    unregister_secret(old)


def describe(name: str) -> dict:
    """What the UI may know about a secret: whether it is set, plus the last 4 chars."""
    value = get(name)
    if not value:
        return {"set": False, "hint": ""}
    return {"set": True, "hint": "…" + value[-4:] if len(value) > 12 else ""}


def load_all() -> None:
    """Register every stored secret with the redactor at startup."""
    for name in NAMES:
        get(name)
