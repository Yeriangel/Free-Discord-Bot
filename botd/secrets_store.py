"""Secrets live in Windows Credential Manager (via keyring), never in files or the DB.

On a server (Linux, Docker) there is usually no credential store: there each key comes from an environment variable
named BOTD_ + the key name in capitals, e.g. BOTD_BOT_TOKEN, BOTD_ANTHROPIC_KEY (see HOSTING.md)."""

from .i18n import tr
import os

import keyring
from keyring.errors import PasswordDeleteError

from .redact import register_secret, unregister_secret

SERVICE = os.environ.get("BOTD_KEYRING_SERVICE", "DiscordBotManager")
NAMES = ("bot_token", "anthropic_key", "openai_key", "xai_key", "twitch_client_id", "twitch_client_secret",
         "roblox_api_key", "discord_user_login", "eulerstream_key", "youtube_api_key", "instagram_api_key")


def _check(name: str) -> None:
    import re

    if name not in NAMES and not re.fullmatch(r"bot_token_[0-9a-f]{6}", name):
        raise KeyError(name)


def env_name(name: str) -> str:
    return "BOTD_" + name.upper()


def _keyring_get(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:  # noqa: BLE001 - no credential store on this machine (a server)
        return None


def from_env(name: str) -> bool:
    return bool(os.environ.get(env_name(name), "").strip())


def get(name: str) -> str | None:
    _check(name)
    value = os.environ.get(env_name(name), "").strip() or _keyring_get(name)
    register_secret(value)
    return value or None


def set(name: str, value: str) -> None:  # noqa: A001 - module-level API
    _check(name)
    value = value.strip()
    if not value:
        raise ValueError(tr("ערך ריק"))
    old = _keyring_get(name)
    register_secret(value)
    try:
        keyring.set_password(SERVICE, name, value)
    except Exception:  # noqa: BLE001
        raise ValueError(tr("אין כאן מקום שמור למפתחות. בשרת, שמים את המפתח במשתנה הסביבה {env}.", env=env_name(name))) from None
    if old and old != value:
        unregister_secret(old)


def delete(name: str) -> None:
    _check(name)
    old = _keyring_get(name)
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass
    except Exception:  # noqa: BLE001 - no credential store
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
