"""Embed messages composed in the app."""

from urllib.parse import urlparse

import discord

from ..i18n import tr
from .welcome import parse_color


def _url(value: str) -> str | None:
    value = (value or "").strip()
    return value if value and urlparse(value).scheme in ("http", "https") else None


def build_embed(cfg: dict) -> discord.Embed:
    if not any((cfg.get(k) or "").strip() for k in ("title", "description", "author_name")) and not cfg.get("fields"):
        raise ValueError(tr("צריך כותרת או תוכן"))
    embed = discord.Embed(title=cfg.get("title") or None, description=cfg.get("description") or None,
                          url=_url(cfg.get("url", "")) if cfg.get("title") else None, color=parse_color(cfg.get("color", "")))
    if cfg.get("author_name"):
        embed.set_author(name=cfg["author_name"][:256], icon_url=_url(cfg.get("author_icon", "")))
    for f in (cfg.get("fields") or [])[:25]:
        if f.get("name") and f.get("value"):
            embed.add_field(name=f["name"][:256], value=f["value"][:1024], inline=bool(f.get("inline")))
    if cfg.get("timestamp"):
        embed.timestamp = discord.utils.utcnow()
    if _url(cfg.get("image_url", "")):
        embed.set_image(url=_url(cfg["image_url"]))
    if _url(cfg.get("thumbnail_url", "")):
        embed.set_thumbnail(url=_url(cfg["thumbnail_url"]))
    if cfg.get("footer"):
        embed.set_footer(text=cfg["footer"][:2048])
    return embed
