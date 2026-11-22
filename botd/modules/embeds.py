"""Embed messages composed in the app."""

from urllib.parse import urlparse

import discord

from ..i18n import tr
from .welcome import parse_color


def _url(value: str) -> str | None:
    value = (value or "").strip()
    return value if value and urlparse(value).scheme in ("http", "https") else None


def build_embed(cfg: dict) -> discord.Embed:
    if not (cfg.get("title") or "").strip() and not (cfg.get("description") or "").strip():
        raise ValueError(tr("צריך כותרת או תוכן"))
    embed = discord.Embed(title=cfg.get("title") or None, description=cfg.get("description") or None,
                          color=parse_color(cfg.get("color", "")))
    if _url(cfg.get("image_url", "")):
        embed.set_image(url=_url(cfg["image_url"]))
    if _url(cfg.get("thumbnail_url", "")):
        embed.set_thumbnail(url=_url(cfg["thumbnail_url"]))
    if cfg.get("footer"):
        embed.set_footer(text=cfg["footer"][:2048])
    return embed
