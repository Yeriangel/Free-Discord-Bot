"""AutoMod rulesets, MEE6-style: every rule has its own switch and action (delete / warn / both)."""

from ..i18n import N_, tr
import logging
import re
import time
import unicodedata
from collections import defaultdict, deque
from functools import lru_cache
from urllib.parse import urlparse

import discord
from discord.ext import commands

from ..bot import USER_PINGS
from .common import COLOR_WARN, mod_embed

log = logging.getLogger("botd.automod")

INVITE_RE = re.compile(r"(?:discord(?:app)?\.com/invite|discord\.gg)/[\w-]+", re.IGNORECASE)
LINK_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w{2,32}:\d{15,21}>")
UNICODE_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
SPOILER_RE = re.compile(r"\|\|.+?\|\|", re.DOTALL)

RULE_REASONS = {
    "words": N_("מילה אסורה"), "repeat": N_("טקסט חוזר"), "invites": N_("הזמנה לשרת אחר"), "links": N_("קישור"),
    "caps": N_("יותר מדי אותיות גדולות"), "emoji": N_("יותר מדי אימוג'ים"), "spoilers": N_("יותר מדי ספוילרים"),
    "mentions": N_("יותר מדי תיוגים"), "zalgo": N_("טקסט משובש (Zalgo)"), "spam": N_("ספאם"),
}


@lru_cache(maxsize=128)
def _words_pattern(words: tuple[str, ...]) -> re.Pattern | None:
    if not words:
        return None
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.IGNORECASE)


def _norm_domain(domain: str) -> str:
    domain = re.sub(r"^https?://", "", domain.strip().lower()).split("/", 1)[0]
    return domain[4:] if domain.startswith("www.") else domain


def has_zalgo(text: str, run: int = 4) -> bool:
    """4+ stacked combining marks on one letter. Hebrew niqqud (at most 2-3 marks) does not trigger it."""
    count = 0
    for ch in text:
        if unicodedata.combining(ch):
            count += 1
            if count >= run:
                return True
        else:
            count = 0
    return False


def count_emoji(text: str) -> int:
    return len(CUSTOM_EMOJI_RE.findall(text)) + len(UNICODE_EMOJI_RE.findall(CUSTOM_EMOJI_RE.sub("", text)))


def caps_ratio(text: str) -> tuple[int, float]:
    letters = [c for c in text if c.isascii() and c.isalpha()]
    if not letters:
        return 0, 0.0
    return len(letters), sum(c.isupper() for c in letters) / len(letters) * 100


def check_content(content: str, cfg: dict, mention_count: int = 0) -> str | None:
    """Stateless rules. Returns the key of the first rule broken, or None."""
    r = cfg
    if r["invites"]["on"] and INVITE_RE.search(content):
        return "invites"
    if r["links"]["on"]:
        allowed = {_norm_domain(d) for d in r["links"]["allowed"] if d.strip()}
        for match in LINK_RE.finditer(content):
            url = match.group(0)
            host = (urlparse(url if "://" in url else "http://" + url).hostname or "").lower()
            host = host[4:] if host.startswith("www.") else host
            if INVITE_RE.search(url) and not r["invites"]["on"]:
                continue
            if not any(host == d or host.endswith("." + d) for d in allowed):
                return "links"
    if r["words"]["on"]:
        pattern = _words_pattern(tuple(w.strip() for w in r["words"]["list"] if w.strip()))
        if pattern is not None and pattern.search(content):
            return "words"
    if r["caps"]["on"]:
        n, pct = caps_ratio(content)
        if n >= 10 and pct > r["caps"]["percent"]:
            return "caps"
    if r["emoji"]["on"] and count_emoji(content) > r["emoji"]["limit"]:
        return "emoji"
    if r["spoilers"]["on"] and len(SPOILER_RE.findall(content)) > r["spoilers"]["limit"]:
        return "spoilers"
    if r["mentions"]["on"] and mention_count > r["mentions"]["limit"]:
        return "mentions"
    if r["zalgo"]["on"] and has_zalgo(content):
        return "zalgo"
    return None


class SpamTracker:
    def __init__(self):
        self._hits: dict[tuple[int, int], deque] = defaultdict(deque)

    def hit(self, key: tuple[int, int], count: int, seconds: float, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        q.append(now)
        while q and now - q[0] > seconds:
            q.popleft()
        if len(q) >= count:
            q.clear()
            return True
        if len(self._hits) > 5000:
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 300]:
                del self._hits[k]
        return False


class RepeatTracker:
    """Same text sent N times in a row (within 2 minutes)."""

    def __init__(self):
        self._last: dict[tuple[int, int], tuple[str, int, float]] = {}

    def hit(self, key: tuple[int, int], text: str, count: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        norm = " ".join(text.lower().split())
        if not norm:
            return False
        prev = self._last.get(key)
        n = prev[1] + 1 if prev and prev[0] == norm and now - prev[2] < 120 else 1
        self._last[key] = (norm, n, now)
        if n >= count:
            self._last.pop(key, None)
            return True
        return False


class AutoMod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.spam = SpamTracker()
        self.repeat = RepeatTracker()
        self._notified: dict[tuple[int, int], float] = {}

    @staticmethod
    def _exempt(message: discord.Message, cfg: dict) -> bool:
        member = message.author
        if not isinstance(member, discord.Member):
            return True
        perms = member.guild_permissions
        if perms.manage_messages or perms.administrator:
            return True
        if {str(r.id) for r in member.roles} & set(cfg["exempt_role_ids"]):
            return True
        ids = {str(message.channel.id)}
        parent = getattr(message.channel, "parent_id", None)
        if parent:
            ids.add(str(parent))
        return bool(ids & set(cfg["ignored_channel_ids"]))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.id == getattr(self.bot.user, "id", None):
            return
        cfg = self.bot.cfg(message.guild, "automod")
        if not cfg or (message.author.bot and cfg["ignore_bots"]) or self._exempt(message, cfg):
            return
        mentions = len(message.raw_mentions) + len(message.raw_role_mentions) + (1 if message.mention_everyone else 0)
        rule = check_content(message.content, cfg, mentions)
        key = (message.guild.id, message.author.id)
        if rule is None and cfg["repeat"]["on"] and self.repeat.hit(key, message.content, cfg["repeat"]["count"]):
            rule = "repeat"
        if rule is None and cfg["spam"]["on"] and self.spam.hit(key, cfg["spam"]["count"], cfg["spam"]["seconds"]):
            rule = "spam"
        if rule is not None:
            await self._act(message, rule, cfg[rule]["action"])

    async def _act(self, message: discord.Message, rule: str, action: str) -> None:
        guild, member = message.guild, message.author
        lang = self.bot.lang(guild)
        reason = tr(RULE_REASONS[rule], lang)
        done = []
        if action in ("delete", "both"):
            try:
                await message.delete()
                done.append(tr("ההודעה נמחקה", lang))
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                log.warning(tr("אוטומוד לא הצליח למחוק הודעה בשרת %s: %s"), guild.name, exc.text or exc.status)
        warned = None
        if action in ("warn", "both") and isinstance(member, discord.Member):
            warn_cog = self.bot.get_cog("Warnings")
            warned = await warn_cog.add_warning(guild, member, tr("אוטומוד: {reason}", lang, reason=reason)) if warn_cog else None
            done.append(tr("אזהרה #{id}", lang, id=warned[0]) if warned else tr("אזהרה (מערכת האזהרות כבויה, לא נרשמה)", lang))
        key = (guild.id, member.id)
        if time.monotonic() - self._notified.get(key, 0) > 10:
            self._notified[key] = time.monotonic()
            what = tr("ההודעה שלך נמחקה", lang) if action != "warn" else tr("קיבלת אזהרה", lang)
            try:
                await message.channel.send(f"{member.mention}, {what} ({reason}).", delete_after=8,
                                           allowed_mentions=USER_PINGS)
            except discord.HTTPException:
                pass
        log.info(tr("אוטומוד בשרת %s: %s · %s"), guild.name, reason, ", ".join(done))
        if not warned:  # a recorded warning already writes its own log entry
            await self.bot.send_log(guild, mod_embed(
                tr("🤖 אוטומוד", lang), member, None, reason, COLOR_WARN, lang=lang,
                extra=f"{', '.join(done)} · {message.channel.mention}\n>>> {message.content[:500]}"), "mod_actions")
