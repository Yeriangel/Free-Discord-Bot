"""Social alerts: new YouTube videos and live streams, Twitch and Kick streams going live, new Reddit, Bluesky and
Telegram posts, and any RSS/Atom feed. Each alert can announce new posts, going live, or both, with or without a link.
TikTok, Instagram and X (Twitter) are not supported: they block automatic reading and have no free feed or API."""

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
        title_text = _text(it, "title") or " ".join(re.sub(r"<[^>]+>", "", _text(it, "description")).split())[:150]
        items.append({"id": _text(it, "guid") or url or _text(it, "title"), "title": title_text,
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


def twitch_login(source: str) -> str:
    """'https://www.twitch.tv/Name?x=1', 'twitch.tv/name', '@name' or 'name' -> 'name'."""
    source = source.strip().split("?")[0].rstrip("/")
    return source.split("/")[-1].lstrip("@").lower()


def decapi_live(uptime_text: str) -> bool:
    """decapi.me answers '<name> is offline' when not streaming, and the uptime ('1 hour, 5 minutes') when live."""
    text = (uptime_text or "").strip().lower()
    return bool(text) and not text.endswith("is offline") and "not found" not in text and "error" not in text \
        and "no user" not in text


def fill_alert(template: str, name: str, title: str, url: str) -> str:
    return template.replace("{name}", name).replace("{title}", title).replace("{url}", url)[:1900]


LIVE_KINDS = ("twitch", "kick")          # only "went live"
BOTH_KINDS = ("youtube",)                # new videos and/or live
PLATFORM = {"twitch": ("Twitch", 0x9146FF), "kick": ("Kick", 0x53FC18), "youtube": ("YouTube", 0xFF0000)}
TG_POST = re.compile(r'data-post="([\w]+/\d+)"')
TG_TEXT = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)


def wants(feed: dict, event: str) -> bool:
    """Does this alert announce 'new' posts / going 'live'?"""
    kind = feed["kind"]
    if kind in LIVE_KINDS:
        return event == "live"
    if kind in BOTH_KINDS:
        return (feed.get("event") or "new") in (event, "both")
    return event == "new"


def simple_name(source: str) -> str:
    """'https://kick.com/Name?x', '@name' or 'name' -> 'name'."""
    return source.strip().split("?")[0].rstrip("/").split("/")[-1].lstrip("@").lower()


def bluesky_handle(source: str) -> str:
    handle = simple_name(source)
    return handle if "." in handle else handle + ".bsky.social"


def parse_telegram(html_text: str, channel: str) -> list[dict]:
    """Posts of a public Telegram channel from its web preview (t.me/s/name), newest first."""
    import html as html_mod

    ids = TG_POST.findall(html_text)
    texts = TG_TEXT.findall(html_text)
    items = []
    for i, post in enumerate(ids):
        raw = texts[i] if i < len(texts) and len(texts) == len(ids) else ""
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", raw.replace("<br/>", "\n").replace("<br>", "\n"))).strip()
        items.append({"id": post, "title": " ".join(text.split())[:200] or post, "url": f"https://t.me/{post}", "author": channel})
    return list(reversed(items))


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
                        self._status(guild, feed, False, f"{type(exc).__name__}: {exc}")
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
        if kind == "bluesky":
            handle = bluesky_handle(source)
            resp = await client.get(f"https://bsky.app/profile/{quote(handle)}/rss")
            resp.raise_for_status()
            title, items = parse_feed(resp.text)
            return title or handle, [{**i, "author": i["author"] or handle} for i in items]
        if kind == "telegram":
            channel = simple_name(source)
            if not re.fullmatch(r"[a-z0-9_]{4,32}", channel):
                raise ValueError(tr("שם ערוץ טלגרם לא תקין: {name}", name=channel))
            resp = await client.get(f"https://t.me/s/{channel}")
            resp.raise_for_status()
            return channel, parse_telegram(resp.text, channel)
        if kind == "rss":
            if urlparse(source).scheme not in ("http", "https"):
                raise ValueError(tr("קישור RSS צריך להתחיל ב-https://"))
            resp = await client.get(source)
            resp.raise_for_status()
            return parse_feed(resp.text)
        raise ValueError(kind)

    async def twitch_status(self, client: httpx.AsyncClient, login: str) -> dict:
        """Whether a Twitch channel is live, with title, game and a preview picture.
        Uses your own Twitch app keys when they are set, otherwise the free decapi.me service (no keys needed)."""
        if not re.fullmatch(r"[a-z0-9_]{3,25}", login):
            raise ValueError(tr("שם משתמש Twitch לא תקין: {name}", name=login))
        url = f"https://twitch.tv/{login}"
        cid, secret = secrets_store.get("twitch_client_id"), secrets_store.get("twitch_client_secret")
        if cid and secret:
            name, items = await self.twitch_helix(client, login, cid, secret)
            if not items:
                return {"live": False, "name": name, "url": url, "title": "", "game": "", "stream_id": "", "thumb": "", "via": "api"}
            s = items[0]
            return {"live": True, "name": s["author"], "url": url, "title": s["title"], "game": s.get("game", ""),
                    "stream_id": s["id"], "thumb": s.get("thumb", ""), "viewers": s.get("viewers"), "via": "api"}
        base = "https://decapi.me/twitch"
        up = await client.get(f"{base}/uptime/{login}")
        up.raise_for_status()
        live = decapi_live(up.text)
        title = game = ""
        if live:
            title = (await client.get(f"{base}/title/{login}")).text.strip()[:250]
            game = (await client.get(f"{base}/game/{login}")).text.strip()[:100]
        return {"live": live, "name": login, "url": url, "title": title, "game": game, "stream_id": "",
                "thumb": f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}-640x360.jpg" if live else "", "via": "decapi"}

    async def kick_status(self, client: httpx.AsyncClient, slug: str) -> dict:
        if not re.fullmatch(r"[a-z0-9_-]{2,40}", slug):
            raise ValueError(tr("שם ערוץ Kick לא תקין: {name}", name=slug))
        resp = await client.get(f"https://kick.com/api/v2/channels/{slug}")
        if resp.status_code == 404:
            raise ValueError(tr("ערוץ Kick לא נמצא: {name}", name=slug))
        resp.raise_for_status()
        data = resp.json()
        live = data.get("livestream") or None
        name = (data.get("user") or {}).get("username") or slug
        cats = (live or {}).get("categories") or []
        thumb = ((live or {}).get("thumbnail") or {}).get("url", "") if isinstance((live or {}).get("thumbnail"), dict) else ""
        return {"live": bool(live), "name": name, "url": f"https://kick.com/{slug}", "title": (live or {}).get("session_title", ""),
                "game": cats[0].get("name", "") if cats else "", "stream_id": str((live or {}).get("id", "")),
                "thumb": thumb, "viewers": (live or {}).get("viewer_count"), "via": "kick", "platform": "kick"}

    async def youtube_live_status(self, client: httpx.AsyncClient, source: str) -> dict:
        cid = youtube_channel_id(source) or self.bot.store.get_kv(f"ytid:{source}")
        base = f"https://www.youtube.com/channel/{cid}" if cid else youtube_page_url(source).rstrip("/")
        page = (await client.get(base + "/live")).text
        live = '"isLive":true' in page
        vid = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', page)
        title = re.search(r'<meta name="title" content="([^"]*)"', page)
        owner = re.search(r'"ownerChannelName":"([^"]+)"', page)
        video = vid.group(1) if vid and live else ""
        import html as html_mod

        return {"live": live and bool(video), "name": owner.group(1) if owner else source, "title": html_mod.unescape(title.group(1)) if title and live else "",
                "url": f"https://www.youtube.com/watch?v={video}" if video else base, "game": "", "stream_id": video,
                "thumb": f"https://i.ytimg.com/vi/{video}/hqdefault_live.jpg" if video else "", "via": "page", "platform": "youtube"}

    async def live_status(self, client: httpx.AsyncClient, feed: dict) -> dict:
        kind = feed["kind"]
        if kind == "twitch":
            return {**await self.twitch_status(client, twitch_login(feed["source"])), "platform": "twitch"}
        if kind == "kick":
            return await self.kick_status(client, simple_name(feed["source"]))
        return await self.youtube_live_status(client, feed["source"].strip())

    async def twitch(self, client: httpx.AsyncClient, login: str) -> tuple[str, list[dict]]:
        cid, secret = secrets_store.get("twitch_client_id"), secrets_store.get("twitch_client_secret")
        return await self.twitch_helix(client, login, cid, secret)

    async def twitch_helix(self, client: httpx.AsyncClient, login: str, cid: str, secret: str) -> tuple[str, list[dict]]:
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
        items = [{"id": s["id"], "title": s.get("title", ""), "url": f"https://twitch.tv/{login}", "author": s.get("user_name", login),
                  "game": s.get("game_name", ""), "viewers": s.get("viewer_count"),
                  "thumb": (s.get("thumbnail_url") or "").replace("{width}", "640").replace("{height}", "360")}
                 for s in streams]
        return (streams[0].get("user_name", login) if streams else login), items

    def _status(self, guild: discord.Guild, feed: dict, ok: bool, msg: str) -> None:
        key = f"feed_status:{guild.id}:{feed['kind']}:{feed['source'].strip().lower()}"
        self.bot.store.set_kv(key, {"ok": ok, "at": time.time(), "msg": msg[:300]})

    def twitch_embed(self, info: dict, lang) -> discord.Embed:
        from .common import themed

        platform, color = PLATFORM.get(info.get("platform", "twitch"), PLATFORM["twitch"])
        embed = discord.Embed(title=info["title"] or info["url"], url=info["url"], color=themed(lang, color))
        embed.set_author(name=tr("🔴 {name} בשידור חי ב-{platform}", lang, name=info["name"], platform=platform), url=info["url"])
        if info.get("game"):
            embed.add_field(name=tr("משחק", lang), value=info["game"][:100])
        if info.get("viewers") is not None:
            embed.add_field(name=tr("צופים", lang), value=str(info["viewers"]))
        if info.get("thumb"):
            embed.set_image(url=f"{info['thumb']}?t={int(time.time())}")  # no stale cached preview
        return embed

    async def check_live(self, client: httpx.AsyncClient, guild: discord.Guild, feed: dict) -> None:
        info = await self.live_status(client, feed)
        slug = twitch_login(feed["source"]) if feed["kind"] == "twitch" else simple_name(feed["source"])
        key = f"twitch_live:{guild.id}:{slug}" if feed["kind"] == "twitch" else f"live:{feed['kind']}:{guild.id}:{slug}"
        previous = self.bot.store.get_kv(key)
        marker = info["stream_id"] or ("live" if info["live"] else "")
        self.bot.store.set_kv(key, marker)
        self._status(guild, feed, True, (tr("🔴 בשידור: {title}", title=info["title"]) if info["live"] else tr("⚫ לא בשידור")))
        if previous is None and info["live"]:
            return  # was already live when the alert was added: do not announce an old stream
        if not info["live"] or marker == previous:
            return
        await self.post(guild, feed, info["name"], info["title"], info["url"], embed=self.twitch_embed(info, self.bot.lang(guild)))

    async def post(self, guild: discord.Guild, feed: dict, name: str, title: str, url: str, embed=None, prefix: str = "") -> None:
        channel = text_channel(guild, feed["channel_id"])
        if channel is None:
            raise ValueError(tr("ערוץ ההתראות לא נמצא"))
        role = f"<@&{feed['role_id']}> " if feed.get("role_id") else ""
        mentions = discord.AllowedMentions(roles=[discord.Object(int(feed["role_id"]))]) if feed.get("role_id") \
            else discord.AllowedMentions.none()
        if (feed.get("style") or "full") == "text":  # only announce: no link, no preview
            body = fill_alert(feed["message"] or "{title}", name, title, "")
            body = "\n".join(line.rstrip() for line in body.splitlines() if line.strip())
            embed = None
        else:
            body = fill_alert(feed["message"] or "{title}\n{url}", name, title, url)
        text = prefix + role + body
        await channel.send(text[:2000], embed=embed, allowed_mentions=mentions)

    async def preview(self, guild: discord.Guild, feed: dict, send: bool = False) -> str:
        """What the source shows right now (for the app's Test button). With send=True also posts a test alert."""
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as client:
            if not wants(feed, "new"):
                info = await self.live_status(client, feed)
                text = (tr("🔴 בשידור עכשיו: {title} ({game})", title=info["title"], game=info["game"] or "-") if info["live"]
                        else tr("⚫ {name} לא בשידור כרגע. ההתראה תישלח כשהשידור יתחיל.", name=info["name"]))
                if info.get("via") == "decapi":
                    text += " " + tr("(בלי מפתחות Twitch, דרך decapi.me)")
                if send:
                    sample = info if info["live"] else {**info, "title": tr("שידור לדוגמה"), "game": info["game"]}
                    await self.post(guild, feed, info["name"], sample["title"], info["url"],
                                    embed=self.twitch_embed(sample, self.bot.lang(guild)), prefix="🧪 ")
                return text
            name, items = await self.fetch(client, feed)
        if not items:
            return tr("{name}: אין פריטים עדיין", name=name or feed["source"])
        top = items[0]
        if send:
            await self.post(guild, feed, top["author"] or name, top["title"], top["url"], prefix="🧪 ")
        return tr("{name}: נמצאו {n} פריטים. האחרון: {title}", name=name or feed["source"], n=len(items), title=top["title"][:150])

    async def check(self, client: httpx.AsyncClient, guild: discord.Guild, feed: dict) -> None:
        if wants(feed, "live"):
            await self.check_live(client, guild, feed)
        if not wants(feed, "new"):
            return
        name, items = await self.fetch(client, feed)
        self._status(guild, feed, True, tr("{n} פריטים, האחרון: {title}", n=len(items), title=(items[0]["title"] if items else "-")[:120]))
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
        for item in reversed(new):
            await self.post(guild, feed, item["author"] or name, item["title"], item["url"])
