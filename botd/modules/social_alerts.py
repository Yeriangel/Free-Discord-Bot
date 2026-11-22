"""Social alerts: new YouTube videos, Twitch streams going live, new Reddit posts and any RSS/Atom feed.
TikTok and X (Twitter) are not supported: they have no free public feed or API."""

from ..i18n import tr
import logging
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse

import discord
import httpx
from discord.ext import commands, tasks

from .. import secrets_store
from .common import text_channel

log = logging.getLogger("botd.social")
UA = "FreeDiscordBot/1.0 (+https://github.com/Yeriangel/Free-Discord-Bot)"
SEEN_KEEP = 60
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
YT_ID = re.compile(r"^UC[\w-]{22}$")


def _text(el, path: str, default: str = "") -> str:
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else default


def parse_feed(xml_text: str) -> tuple[str, list[dict]]:
    """RSS 2.0 or Atom -> (feed title, items newest first as {id, title, url, author})."""
    root = ET.fromstring(xml_text)
    items = []
    if root.tag == ATOM + "feed":
        title = _text(root, ATOM + "title")
        for e in root.findall(ATOM + "entry"):
            link = e.find(ATOM + "link[@rel='alternate']") or e.find(ATOM + "link")
            url = link.get("href", "") if link is not None else ""
            items.append({"id": _text(e, YT + "videoId") or _text(e, ATOM + "id") or url,
                          "title": _text(e, ATOM + "title"), "url": url,
                          "author": _text(e, f"{ATOM}author/{ATOM}name")})
        return title, items
    channel = root.find("channel")
    if channel is None:
        raise ValueError("not an RSS or Atom feed")
    title = _text(channel, "title")
    for it in channel.findall("item"):
        url = _text(it, "link")
        items.append({"id": _text(it, "guid") or url or _text(it, "title"), "title": _text(it, "title"),
                      "url": url, "author": _text(it, "author")})
    return title, items


def youtube_channel_id(source: str) -> str | None:
    """Channel ID straight from the text, when it holds one (UC..., or a /channel/UC... link)."""
    source = source.strip()
    if YT_ID.match(source):
        return source
    m = re.search(r"/channel/(UC[\w-]{22})", source)
    return m.group(1) if m else None


def youtube_page_url(source: str) -> str:
    source = source.strip()
    if source.startswith("http"):
        return source
    return "https://www.youtube.com/" + (source if source.startswith("@") else "@" + source)


def reddit_sub(source: str) -> str:
    m = re.search(r"(?:reddit\.com)?/?r/([A-Za-z0-9_]{2,21})", source)
    return m.group(1) if m else source.strip().lstrip("r/").strip("/")


def fill_alert(template: str, name: str, title: str, url: str) -> str:
    return template.replace("{name}", name).replace("{title}", title).replace("{url}", url)[:1900]


class SocialAlerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._twitch_token: tuple[str, float] | None = None
        self.loop.start()

    async def cog_unload(self) -> None:
        self.loop.cancel()

    @tasks.loop(minutes=1)
    async def loop(self) -> None:
        now = time.time()
        for guild in self.bot.guilds:
            cfg = self.bot.cfg(guild, "social_alerts")
            if not cfg or not cfg["feeds"]:
                continue
            key = f"social_last:{guild.id}"
            if now - self.bot.store.get_kv(key, 0) < cfg["check_minutes"] * 60:
                continue
            self.bot.store.set_kv(key, now)
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as client:
                for feed in cfg["feeds"]:
                    try:
                        await self.check(client, guild, feed)
                    except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the others
                        log.warning(tr("בדיקת {kind} '{source}' נכשלה: {error}", kind=feed["kind"], source=feed["source"],
                                       error=f"{type(exc).__name__}: {exc}"[:200]))

    @loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    async def fetch(self, client: httpx.AsyncClient, feed: dict) -> tuple[str, list[dict]]:
        kind, source = feed["kind"], feed["source"].strip()
        if kind == "youtube":
            cid = youtube_channel_id(source) or self.bot.store.get_kv(f"ytid:{source}")
            if not cid:
                page = (await client.get(youtube_page_url(source))).text
                m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page)
                if not m:
                    raise ValueError(tr("לא מצאתי את ערוץ ה-YouTube"))
                cid = m.group(1)
                self.bot.store.set_kv(f"ytid:{source}", cid)
            resp = await client.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
            resp.raise_for_status()
            return parse_feed(resp.text)
        if kind == "reddit":
            sub = reddit_sub(source)
            resp = await client.get(f"https://www.reddit.com/r/{quote(sub)}/new/.rss")
            resp.raise_for_status()
            _title, items = parse_feed(resp.text)
            return f"r/{sub}", items
        if kind == "rss":
            if urlparse(source).scheme not in ("http", "https"):
                raise ValueError(tr("קישור RSS צריך להתחיל ב-https://"))
            resp = await client.get(source)
            resp.raise_for_status()
            return parse_feed(resp.text)
        if kind == "twitch":
            return await self.twitch(client, source.rstrip("/").split("/")[-1].lower())
        raise ValueError(kind)

    async def twitch(self, client: httpx.AsyncClient, login: str) -> tuple[str, list[dict]]:
        cid, secret = secrets_store.get("twitch_client_id"), secrets_store.get("twitch_client_secret")
        if not cid or not secret:
            raise ValueError(tr("ל-Twitch צריך Client ID ו-Client Secret בעמוד 'חיבור והגדרות'"))
        if self._twitch_token is None or self._twitch_token[1] < time.time() + 60:
            resp = await client.post("https://id.twitch.tv/oauth2/token",
                                     data={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
            resp.raise_for_status()
            data = resp.json()
            self._twitch_token = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
        resp = await client.get("https://api.twitch.tv/helix/streams", params={"user_login": login},
                                headers={"Client-Id": cid, "Authorization": f"Bearer {self._twitch_token[0]}"})
        resp.raise_for_status()
        streams = resp.json().get("data", [])
        items = [{"id": s["id"], "title": s.get("title", ""), "url": f"https://twitch.tv/{login}", "author": s.get("user_name", login)}
                 for s in streams]
        return (streams[0].get("user_name", login) if streams else login), items

    async def check(self, client: httpx.AsyncClient, guild: discord.Guild, feed: dict) -> None:
        name, items = await self.fetch(client, feed)
        key = f"feed_seen:{guild.id}:{feed['kind']}:{feed['source'].strip().lower()}"
        seen = self.bot.store.get_kv(key)
        ids = [i["id"] for i in items]
        if seen is None:  # first look: remember what is already there, post nothing
            self.bot.store.set_kv(key, ids[:SEEN_KEEP])
            return
        new = [i for i in items if i["id"] not in seen][:3]
        if not new:
            return
        self.bot.store.set_kv(key, (ids + [s for s in seen if s not in ids])[:SEEN_KEEP])
        channel = text_channel(guild, feed["channel_id"])
        if channel is None:
            return
        role = f"<@&{feed['role_id']}> " if feed.get("role_id") else ""
        mentions = discord.AllowedMentions(roles=[discord.Object(int(feed["role_id"]))]) if feed.get("role_id") \
            else discord.AllowedMentions.none()
        for item in reversed(new):
            text = role + fill_alert(feed["message"] or "{title}\n{url}", item["author"] or name, item["title"], item["url"])
            await channel.send(text[:2000], allowed_mentions=mentions)
