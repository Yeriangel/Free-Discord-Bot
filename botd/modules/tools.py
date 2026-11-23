"""Info and tool commands: /roblox-user, /roblox-cloth, /pokedex, /morse, /binary, /qr, /gif, /opacity, /file-format,
/seed, /pfp, /time, /clear, plus "Make GIF" (right click on a message) and "!gif" as a reply.

They work in servers (when the module is on there) and also as a user app ("Add App"): installed to a Discord
account, they can be used in DMs and in any server, even where the bot itself is not a member.
"""

from ..i18n import N_, tr, ui_lang
import asyncio
import datetime as dt
import io
import re
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from .. import images, qr, roblox_assets, secrets_store, wintz
from .common import COLOR_INFO, themed

log = logging.getLogger("botd.tools")

EVERYWHERE = dict(guilds=True, dms=True, private_channels=True)
HTTP_HEADERS = {"User-Agent": "FreeDiscordBot/1.0 (+https://github.com/Yeriangel/Free-Discord-Bot)"}

# ---- Morse and binary ------------------------------------------------------------------------------------------
MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....", "I": "..",
    "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.", "!": "-.-.--",
    "/": "-..-.", "(": "-.--.", ")": "-.--.-", "&": ".-...", ":": "---...", ";": "-.-.-.", "=": "-...-",
    "+": ".-.-.", "-": "-....-", "_": "..--.-", '"': ".-..-.", "$": "...-..-", "@": ".--.-.",
}
FROM_MORSE = {v: k for k, v in MORSE.items()}


def is_morse(text: str) -> bool:
    return bool(text.strip()) and set(text.strip()) <= set(".-/ |") and any(c in text for c in ".-")


def to_morse(text: str) -> tuple[str, list[str]]:
    """Text -> Morse (letters split by spaces, words by ' / '). Also returns the characters Morse has no code for."""
    words, skipped = [], []
    for word in text.upper().split():
        codes = []
        for ch in word:
            if ch in MORSE:
                codes.append(MORSE[ch])
            elif ch not in skipped:
                skipped.append(ch)
        if codes:
            words.append(" ".join(codes))
    return " / ".join(words), skipped


def from_morse(code: str) -> str:
    words = code.replace("|", "/").split("/")
    return " ".join("".join(FROM_MORSE.get(c, "?") for c in w.split()) for w in words if w.strip())


def is_binary(text: str) -> bool:
    bits = text.replace(" ", "")
    return bool(bits) and set(bits) <= {"0", "1"} and len(bits) % 8 == 0


def to_binary(text: str) -> str:
    return " ".join(f"{b:08b}" for b in text.encode("utf-8"))


def from_binary(bits: str) -> str:
    raw = bits.replace(" ", "")
    return bytes(int(raw[i:i + 8], 2) for i in range(0, len(raw), 8)).decode("utf-8", errors="replace")


# ---- QR payloads (like an online QR generator: link, text, Wi-Fi, email, phone, SMS) ---------------------------
def _wifi_escape(value: str) -> str:
    return "".join("\\" + c if c in '\\;,:"' else c for c in value)


def qr_payload(kind: str, content: str, extra: str = "", security: str = "WPA") -> str:
    content = content.strip()
    if kind == "url":
        return content if "://" in content else "https://" + content
    if kind == "wifi":
        sec = security if security in ("WPA", "WEP", "nopass") else "WPA"
        password = "" if sec == "nopass" else f"P:{_wifi_escape(extra)};"
        return f"WIFI:T:{sec};S:{_wifi_escape(content)};{password};"
    if kind == "email":
        return f"mailto:{content}" + (f"?subject={extra}" if extra else "")
    if kind == "phone":
        return "tel:" + content.replace(" ", "")
    if kind == "sms":
        return f"SMSTO:{content.replace(' ', '')}:{extra}"
    return content  # plain text


# ---- GIF (ffmpeg) -------------------------------------------------------------------------------------------------
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_GIF_BYTES = 10 * 1024 * 1024  # Discord's upload limit without boosts
VIDEO_TYPES = ("video/", "image/gif")


def find_ffmpeg() -> str | None:
    """ffmpeg on PATH, or where winget puts it (winget install ffmpeg)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    for candidate in [base / "Links" / "ffmpeg.exe", *sorted((base / "Packages").glob("*FFmpeg*/**/bin/ffmpeg.exe"))]:
        if candidate.is_file():
            return str(candidate)
    return None


async def video_to_gif(ffmpeg: str, src: Path, dst: Path, width: int, fps: int, seconds: int) -> None:
    filters = (f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
               "[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-t", str(seconds), "-i", str(src), "-vf", filters,
        "-loop", "0", str(dst), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("timeout")
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError((err or b"").decode(errors="replace")[-300:])


# ---- Minecraft seed map (Chunkbase) -------------------------------------------------------------------------------
# Chunkbase's own platform names (from its version list); the first of each edition is the newest.
SEED_PLATFORMS = [
    ("java_26_3", "Java 26.3"), ("java_26_2", "Java 26.2"), ("java_26_1", "Java 26.1"),
    ("java_1_21_9", "Java 1.21.9 - 1.21.11"), ("java_1_21_6", "Java 1.21.6 - 1.21.8"), ("java_1_21_4", "Java 1.21.4"),
    ("java_1_21", "Java 1.21 - 1.21.1"), ("java_1_20", "Java 1.20"), ("java_1_19", "Java 1.19 - 1.19.2"),
    ("java_1_18", "Java 1.18"), ("java_1_16", "Java 1.16"), ("java_1_12", "Java 1.12"), ("java_1_8", "Java 1.8"),
    ("bedrock_26_60", "Bedrock 26.60 - 26.65"), ("bedrock_26_50", "Bedrock 26.50"), ("bedrock_26_30", "Bedrock 26.30"),
    ("bedrock_26_0", "Bedrock 26.0"), ("bedrock_1_21_120", "Bedrock 1.21.120"), ("bedrock_1_21_90", "Bedrock 1.21.90"),
    ("bedrock_1_21", "Bedrock 1.21"), ("bedrock_1_20", "Bedrock 1.20"), ("bedrock_1_19", "Bedrock 1.19"),
    ("bedrock_1_18", "Bedrock 1.18"),
]
DIMENSIONS = [("overworld", N_("העולם הרגיל (Overworld)")), ("nether", "The Nether"), ("end", "The End")]


def seed_map_url(seed: str, platform: str = "java_26_3", dimension: str = "overworld", x: int = 0, z: int = 0) -> str:
    from urllib.parse import quote

    return (f"https://www.chunkbase.com/apps/seed-map#seed={quote(seed.strip(), safe='-')}&platform={platform}"
            f"&dimension={dimension}&view=biomes.hillshade&x={x}&z={z}&zoom=0.5")


# ---- /time ----------------------------------------------------------------------------------------------------------
TIME_STYLES = [("t", N_("שעה קצרה")), ("T", N_("שעה עם שניות")), ("d", N_("תאריך קצר")), ("D", N_("תאריך ארוך")),
               ("f", N_("תאריך ושעה")), ("F", N_("יום, תאריך ושעה")), ("R", N_("כמה זמן נשאר / עבר"))]
TIME_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?$", re.I)


def parse_clock(text: str) -> tuple[int, int] | None:
    """'21:00', '9pm', '9:30 PM', '2130' -> (hour, minute)."""
    t = text.strip().lower().replace(" ", "")
    if re.fullmatch(r"\d{3,4}", t):
        t = t[:-2] + ":" + t[-2:]
    m = TIME_RE.match(t)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    return (hour, minute) if hour < 24 and minute < 60 else None


def parse_day(text: str, today: dt.date) -> dt.date | None:
    """'today', 'tomorrow', '24/12', '24/12/2026', '2026-12-24' (also 24.12 and 24-12)."""
    t = text.strip().lower()
    if t in ("today", "היום", "сегодня", "اليوم"):  # i18n: ignore (words people may type)
        return today
    if t in ("tomorrow", "מחר", "завтра", "غدا", "غدًا"):  # i18n: ignore
        return today + dt.timedelta(days=1)
    try:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
        if m:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?", t)
        if m:
            year = int(m.group(3)) if m.group(3) else today.year
            year += 2000 if year < 100 else 0
            day = dt.date(year, int(m.group(2)), int(m.group(1)))
            if not m.group(3) and day < today:
                day = day.replace(year=today.year + 1)  # "24/12" means the next 24/12
            return day
    except ValueError:
        return None
    return None


def event_time(clock: str, zone: str, day: str | None = None) -> tuple[int, str, dt.datetime] | str:
    """-> (unix time, zone label, local time) or an error key ('time' / 'zone' / 'date')."""
    hm = parse_clock(clock)
    if hm is None:
        return "time"
    picked = wintz.resolve(zone)
    if picked is None:
        return "zone"
    kind, value = picked
    now_local = wintz.now_in(kind, value)
    if day:
        date = parse_day(day, now_local.date())
        if date is None:
            return "date"
    else:
        date = now_local.date()
        if (hm[0], hm[1]) <= (now_local.hour, now_local.minute):
            date += dt.timedelta(days=1)  # a time that already passed today means tomorrow
    local = dt.datetime(date.year, date.month, date.day, hm[0], hm[1])
    utc, offset = wintz.to_utc(kind, value, local)
    unix = int(utc.replace(tzinfo=dt.timezone.utc).timestamp())
    if kind == "zone":  # the English name, whatever language Windows has
        place = wintz.english_city(value) or value.replace(" Standard Time", "")
        return unix, f"{place} ({wintz.fmt_offset(offset)})", local
    return unix, value, local


# ---- /roblox-cloth --------------------------------------------------------------------------------------------------
CLOTH_TYPES = {2: "T-Shirt", 11: "Shirt", 12: "Pants"}
TEMPLATE_RE = re.compile(r"<url>[^<]*?(?:[?&]id=|rbxassetid://)(\d+)", re.I)


def template_id(asset_xml: str) -> str | None:
    return roblox_assets.linked_asset_id(asset_xml.encode("utf-8", "replace"))


ROBLOX_PROFILE_RE = re.compile(r"roblox\.com/users/(\d+)", re.I)


def roblox_query(text: str) -> tuple[str, str]:
    """What the user typed -> ('id', '156') or ('name', 'builderman'). Profile links and @names work too."""
    t = text.strip()
    m = ROBLOX_PROFILE_RE.search(t)
    if m:
        return "id", m.group(1)
    t = t.lstrip("@").strip()
    return ("id", t) if t.isdigit() else ("name", t)


def pokemon_slug(text: str) -> str:
    """'Mr. Mime' -> 'mr-mime', 'Farfetch'd' -> 'farfetchd', 'Nidoran♀' -> 'nidoran-f', '#025' -> '25'."""
    t = text.strip().lower().lstrip("#").strip()
    if t.isdigit():
        return str(int(t))
    t = t.replace("♀", "-f").replace("♂", "-m").replace("é", "e")
    t = re.sub(r"[.'’:]", "", t)
    t = re.sub(r"[\s_]+", "-", t)
    return re.sub(r"-+", "-", t).strip("-")


GIF_REPLY_WORDS = ("!gif", "/gif")
MEDIA_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif", ".png", ".jpg", ".jpeg", ".webp")


def media_of(message: discord.Message):
    """The first video or picture attached to a message (or None)."""
    for a in message.attachments:
        ctype = (a.content_type or "").lower()
        if ctype.startswith(("video/", "image/")) or a.filename.lower().endswith(MEDIA_SUFFIXES):
            return a
    return None


# ---- the cog ------------------------------------------------------------------------------------------------------
QR_KINDS = [("url", N_("קישור")), ("text", N_("טקסט")), ("wifi", N_("Wi-Fi")), ("email", N_("אימייל")),
            ("phone", N_("טלפון")), ("sms", N_("SMS"))]


class Tools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pokemon_names: list[tuple[int, str]] = []  # (number, name), loaded once for the /pokedex suggestions
        self.gif_menu = app_commands.ContextMenu(name="Make GIF", callback=self.gif_from_message,
                                                 allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
                                                 allowed_contexts=app_commands.AppCommandContext(
                                                     guild=True, dm_channel=True, private_channel=True))

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.gif_menu)
        asyncio.get_running_loop().create_task(self._load_pokemon_names())

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.gif_menu.name, type=self.gif_menu.type)

    async def _load_pokemon_names(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=20, headers=HTTP_HEADERS) as client:
                r = await client.get("https://pokeapi.co/api/v2/pokemon-species", params={"limit": 2000})
                rows = r.json().get("results", []) if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            return
        names = []
        for row in rows:
            m = re.search(r"/(\d+)/?$", row.get("url", ""))
            if m:
                names.append((int(m.group(1)), row["name"]))
        self.pokemon_names = sorted(names)

    # which language, and is the command allowed here
    def _lang(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is not None and self.bot.get_guild(guild.id) is not None:
            return self.bot.lang(guild)
        return ui_lang()

    async def _allowed(self, interaction: discord.Interaction, name: str) -> bool:
        """In a server where the bot is a member, the server's Tools module settings decide. Used as a user app
        (or in DMs), the person installed it for themselves, so it always works."""
        guild = interaction.guild
        if guild is None or self.bot.get_guild(guild.id) is None:
            return True
        is_guild_install = getattr(interaction, "is_guild_integration", lambda: True)()
        if not is_guild_install:
            return True
        cfg = self.bot.cfg(guild, "tools")
        lang = self.bot.lang(guild)
        if not cfg:
            await interaction.response.send_message(
                tr("המודול הזה כבוי בשרת הזה. אפשר להפעיל אותו באפליקציה.", lang), ephemeral=True)
            return False
        if not cfg.get(name, True):
            await interaction.response.send_message(tr("הפקודה הזו כבויה בשרת הזה.", lang), ephemeral=True)
            return False
        return True

    async def _ffmpeg_or_explain(self, interaction, lang) -> str | None:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            log.warning(tr("ffmpeg לא נמצא: /gif צריך אותו. מתקינים עם הפקודה winget install ffmpeg"))
            await interaction.response.send_message(
                tr("כדי לעבוד עם קבצים, מי שמריץ את הבוט צריך להתקין ffmpeg (בחלון PowerShell: winget install ffmpeg) ולהפעיל את הבוט מחדש.", lang),
                ephemeral=True)
        return ffmpeg

    async def _check_image(self, interaction, lang, file: discord.Attachment) -> bool:
        name = file.filename.lower()
        if name.endswith(".pdf") or (file.content_type or "").startswith("application/pdf"):
            await interaction.response.send_message(
                tr("אי אפשר לפתוח קבצי PDF. אפשר להמיר תמונה ל-PDF, אבל לא PDF לתמונה.", lang), ephemeral=True)
            return False
        if not ((file.content_type or "").startswith("image/") or name.endswith(images.READABLE)):
            await interaction.response.send_message(tr("זה לא קובץ תמונה.", lang), ephemeral=True)
            return False
        if file.size > MAX_IMAGE_BYTES:
            await interaction.response.send_message(tr("הקובץ גדול מדי (עד 25MB).", lang), ephemeral=True)
            return False
        return True

    async def _send_result(self, interaction, lang, path: Path, filename: str) -> None:
        if path.stat().st_size > MAX_GIF_BYTES:
            await interaction.followup.send(tr("הקובץ שיצא גדול מדי לדיסקורד (יותר מ-10MB).", lang))
            return
        await interaction.followup.send(file=discord.File(path, filename))

    # ---- /opacity
    @app_commands.command(name="opacity", description=N_("שינוי השקיפות של תמונה"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(image=N_("התמונה"), percent=N_("אטימות באחוזים: 100 = בלי שינוי, 1 = כמעט שקוף"))
    async def opacity(self, interaction: discord.Interaction, image: discord.Attachment,
                      percent: app_commands.Range[int, 1, 100]):
        if not await self._allowed(interaction, "opacity"):
            return
        lang = self._lang(interaction)
        if not await self._check_image(interaction, lang, image):
            return
        ffmpeg = await self._ffmpeg_or_explain(interaction, lang)
        if not ffmpeg:
            return
        await interaction.response.defer(thinking=True)
        with tempfile.TemporaryDirectory(prefix="botd-img-") as tmp:
            src, dst = Path(tmp) / ("in" + (Path(image.filename).suffix or ".png")), Path(tmp) / "out.png"
            await image.save(src)
            try:
                await images.change_opacity(ffmpeg, src, dst, percent)
            except images.ImageError as exc:
                return await interaction.followup.send(self._image_error(exc, lang))
            await self._send_result(interaction, lang, dst, f"{Path(image.filename).stem[:60] or 'image'}-{percent}.png")

    def _image_error(self, exc, lang) -> str:
        if str(exc) == "too big":
            return tr("התמונה גדולה מדי (עד 40 מגה-פיקסל).", lang)
        return tr("לא הצלחתי לקרוא את התמונה הזו.", lang)

    # ---- /file-format
    @app_commands.command(name="file-format", description=N_("המרת תמונה לפורמט אחר (PNG, JPEG, WEBP, GIF, TIFF, BMP, PSD, PDF)"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(file=N_("התמונה להמרה (PNG, JPG, WEBP, GIF, TIFF, BMP, PSD ועוד)"), to=N_("לאיזה פורמט להמיר"))
    @app_commands.choices(to=[app_commands.Choice(name=key.upper(), value=key) for key in images.FORMATS])
    async def file_format(self, interaction: discord.Interaction, file: discord.Attachment, to: str):
        if not await self._allowed(interaction, "file_format"):
            return
        lang = self._lang(interaction)
        if not await self._check_image(interaction, lang, file):
            return
        ffmpeg = await self._ffmpeg_or_explain(interaction, lang)
        if not ffmpeg:
            return
        await interaction.response.defer(thinking=True)
        with tempfile.TemporaryDirectory(prefix="botd-img-") as tmp:
            src = Path(tmp) / ("in" + (Path(file.filename).suffix.lower() or ".png"))
            await file.save(src)
            try:
                out = await images.convert(ffmpeg, src, to, Path(tmp))
            except images.ImageError as exc:
                return await interaction.followup.send(self._image_error(exc, lang))
            except RuntimeError as exc:
                log.warning("image conversion failed: %s", exc)
                return await interaction.followup.send(tr("לא הצלחתי להמיר את הקובץ.", lang))
            name = f"{Path(file.filename).stem[:60] or 'image'}.{images.FORMATS[to]}"
            await self._send_result(interaction, lang, out, name)

    # ---- /seed
    @app_commands.command(name="seed", description=N_("פתיחת מפת העולם של סיד מיינקראפט ב-Chunkbase"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(seed=N_("הסיד (מספר או טקסט)"), version=N_("גרסת המשחק (ברירת מחדל: Java החדשה ביותר)"),
                           dimension=N_("איזה עולם להראות"), x=N_("קואורדינטת X (לא חובה)"), z=N_("קואורדינטת Z (לא חובה)"))
    @app_commands.choices(version=[app_commands.Choice(name=label, value=key) for key, label in SEED_PLATFORMS],
                          dimension=[app_commands.Choice(name=label, value=key) for key, label in DIMENSIONS])
    async def seed(self, interaction: discord.Interaction, seed: app_commands.Range[str, 1, 64], version: str | None = None,
                   dimension: str | None = None, x: app_commands.Range[int, -30_000_000, 30_000_000] = 0,
                   z: app_commands.Range[int, -30_000_000, 30_000_000] = 0):
        if not await self._allowed(interaction, "seed"):
            return
        lang = self._lang(interaction)
        platform = version or SEED_PLATFORMS[0][0]
        dim = dimension or "overworld"
        url = seed_map_url(seed, platform, dim, x, z)
        label = dict(SEED_PLATFORMS).get(platform, platform)
        embed = discord.Embed(title=tr("מפת סיד: {seed}", lang, seed=seed[:64]), url=url, color=themed(lang, COLOR_INFO),
                              description=f"{label} · {tr(dict(DIMENSIONS)[dim], lang)} · X {x}, Z {z}")
        embed.set_footer(text="Chunkbase")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=tr("פתח ב-Chunkbase", lang), url=url, emoji="🗺️"))
        await interaction.response.send_message(embed=embed, view=view)

    # ---- /pfp
    @app_commands.command(name="pfp", description=N_("תמונת הפרופיל של משתמש בגודל מלא"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(user=N_("המשתמש (אפשר גם להדביק מספר ID)"),
                           server_picture=N_("להראות את התמונה שלו בשרת הזה, אם יש לו כזו"))
    async def pfp(self, interaction: discord.Interaction, user: discord.User, server_picture: bool = False):
        if not await self._allowed(interaction, "pfp"):
            return
        lang = self._lang(interaction)
        asset = user.display_avatar
        if not server_picture:
            asset = user.avatar or user.default_avatar
        elif isinstance(user, discord.Member) and user.guild_avatar:
            asset = user.guild_avatar
        await interaction.response.defer(thinking=True)
        png = asset.with_format("png").with_size(1024) if not asset.url.endswith(".png") or asset.is_animated() \
            else asset.with_size(1024)
        if "embed/avatars" in asset.url:  # Discord's default picture has one size only
            png = asset
        try:
            data = await png.read()
        except discord.HTTPException:
            return await interaction.followup.send(tr("לא הצלחתי להוריד את התמונה.", lang))
        name = re.sub(r"[^\w.-]", "_", user.name)[:40] or "user"
        embed = discord.Embed(title=tr("תמונת הפרופיל של {name}", lang, name=user.display_name), color=themed(lang, COLOR_INFO))
        embed.set_image(url=f"attachment://{name}.png")
        links = [f"[PNG]({png.url})"]
        if "embed/avatars" not in asset.url:
            links += [f"[JPG]({asset.with_format('jpg').with_size(1024).url})", f"[WEBP]({asset.with_format('webp').with_size(1024).url})"]
            if asset.is_animated():
                links.append(f"[GIF]({asset.with_format('gif').with_size(1024).url})")
        embed.description = " · ".join(links)
        embed.set_footer(text=f"ID {user.id}")
        await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(data), f"{name}.png"))

    # ---- /time
    @app_commands.command(name="time", description=N_("שעה של אירוע שכל אחד רואה לפי השעון שלו"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(time=N_("השעה, למשל 21:00 או 9pm"), timezone=N_("אזור הזמן של השעה הזו (חפש עיר, או GMT+3)"),
                           date=N_("התאריך, למשל 24/12 או tomorrow (ברירת מחדל: הפעם הבאה שהשעה הזו מגיעה)"))
    async def time_cmd(self, interaction: discord.Interaction, time: app_commands.Range[str, 1, 12],
                       timezone: app_commands.Range[str, 1, 100], date: app_commands.Range[str, 1, 20] | None = None):
        if not await self._allowed(interaction, "time"):
            return
        lang = self._lang(interaction)
        result = event_time(time, timezone, date)
        if isinstance(result, str):
            msg = {"time": tr("לא הבנתי את השעה. כתוב למשל 21:00 או 9pm.", lang),
                   "zone": tr("לא מצאתי את אזור הזמן. בחר מהרשימה, או כתוב למשל GMT+3.", lang),
                   "date": tr("לא הבנתי את התאריך. כתוב למשל 24/12, 24/12/2026 או tomorrow.", lang)}[result]
            return await interaction.response.send_message(msg, ephemeral=True)
        unix, zone_name, local = result
        embed = discord.Embed(title=f"🕒 {local:%d/%m/%Y %H:%M} · {zone_name}"[:256], color=themed(lang, COLOR_INFO),
                              description=f"## <t:{unix}:F>\n<t:{unix}:R>\n\n" + tr(
                                  "כל אחד רואה את השעה הזו לפי אזור הזמן שלו. כדי לשים אותה בהודעה, העתק את הקוד:", lang))
        for style, label in TIME_STYLES:
            embed.add_field(name=tr(label, lang), value=f"<t:{unix}:{style}>\n`<t:{unix}:{style}>`", inline=True)
        await interaction.response.send_message(embed=embed)

    @time_cmd.autocomplete("timezone")
    async def time_zone_autocomplete(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=name, value=value) for name, value in wintz.search(current)]

    # ---- /roblox-cloth
    @app_commands.command(name="roblox-cloth", description=N_("התבנית (טקסטורה) של חולצה או מכנסיים ברובלוקס"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(item=N_("מספר הפריט (ID) או קישור אליו בקטלוג של רובלוקס"))
    async def roblox_cloth(self, interaction: discord.Interaction, item: app_commands.Range[str, 1, 200]):
        if not await self._allowed(interaction, "roblox_cloth"):
            return
        lang = self._lang(interaction)
        m = re.search(r"(\d{3,20})", item)
        if not m:
            return await interaction.response.send_message(tr("כתוב את מספר הפריט (ID) או הדבק את הקישור שלו.", lang), ephemeral=True)
        aid = m.group(1)
        await interaction.response.defer(thinking=True)
        key = secrets_store.get("roblox_api_key")
        try:
            async with httpx.AsyncClient(timeout=25, headers=HTTP_HEADERS, follow_redirects=True) as client:
                details = await client.get(f"https://economy.roblox.com/v2/assets/{aid}/details")
                info = details.json() if details.status_code == 200 else {}
                type_id = info.get("AssetTypeId")
                if type_id == 1:  # the user already gave the template (an image)
                    kind, tid = "Image", aid
                else:
                    kind = CLOTH_TYPES.get(type_id) or ("Decal" if type_id == 13 else None)
                    if not kind:
                        return await interaction.followup.send(tr("הפריט {id} הוא לא חולצה, מכנסיים או טי-שירט.", lang, id=aid)
                                                               if info else tr("לא נמצא פריט עם המספר {id}.", lang, id=aid))
                    tid = None
                try:
                    if tid is None:
                        tid = roblox_assets.linked_asset_id(await roblox_assets.download(client, aid, key))
                    data = await roblox_assets.download(client, tid, key) if tid else b""
                except roblox_assets.NeedsKey:
                    return await self._cloth_preview(interaction, lang, client, aid, info, kind, bool(key))
        except httpx.HTTPError:
            return await interaction.followup.send(tr("רובלוקס לא עונה כרגע. נסה שוב עוד מעט.", lang))
        if not data.startswith((b"\x89PNG", b"\xff\xd8")):
            return await interaction.followup.send(tr("רובלוקס לא מסר את התבנית של הפריט הזה.", lang))
        ext = "png" if data.startswith(b"\x89PNG") else "jpg"
        name = re.sub(r"[^\w.-]", "_", info.get("Name") or kind)[:50]
        embed = discord.Embed(title=f"{info.get('Name') or kind} · {kind}", url=f"https://www.roblox.com/catalog/{aid}",
                              color=themed(lang, COLOR_INFO))
        creator = (info.get("Creator") or {}).get("Name")
        if creator:
            embed.set_author(name=creator)
        embed.set_image(url=f"attachment://{name}-template.{ext}")
        embed.set_footer(text=tr("תבנית {id}", lang, id=tid))
        await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(data), f"{name}-template.{ext}"))

    async def _cloth_preview(self, interaction, lang, client, aid, info, kind, has_key: bool) -> None:
        """Roblox keeps player-made templates for signed-in requests: show the item's picture and explain."""
        image = await roblox_assets.thumbnail(client, aid)
        embed = discord.Embed(title=f"{info.get('Name') or kind} · {kind}", url=f"https://www.roblox.com/catalog/{aid}",
                              color=themed(lang, COLOR_INFO))
        creator = (info.get("Creator") or {}).get("Name")
        if creator:
            embed.set_author(name=creator)
        if image:
            embed.set_image(url=image)
        if has_key:
            embed.description = tr("מפתח ה-API של רובלוקס לא עבד. בדוק שיש לו הרשאת legacy-asset:manage.", lang)
        else:
            embed.description = tr("רובלוקס נותן את התבנית של פריטים שיצרו שחקנים רק עם מפתח API (חינמי). מי שמריץ את הבוט "
                                   "יכול להוסיף אותו באפליקציה, בעמוד 'חיבור והגדרות'. בינתיים, זו התמונה של הפריט.", lang)
        await interaction.followup.send(embed=embed)

    # ---- /roblox-user
    @app_commands.command(name="roblox-user", description=N_("מידע על משתמש רובלוקס"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(user=N_("שם משתמש, שם תצוגה, מספר (ID) או קישור לפרופיל ברובלוקס"))
    async def roblox_user(self, interaction: discord.Interaction, user: app_commands.Range[str, 1, 100]):
        if not await self._allowed(interaction, "roblox"):
            return
        lang = self._lang(interaction)
        await interaction.response.defer(thinking=True)
        kind, value = roblox_query(user)
        try:
            async with httpx.AsyncClient(timeout=15, headers=HTTP_HEADERS) as client:
                uid = await self._roblox_user_id(client, kind, value)
                if not uid:
                    return await interaction.followup.send(tr("לא נמצא משתמש רובלוקס בשם {name}.", lang, name=user))
                r = await client.get(f"https://users.roblox.com/v1/users/{uid}")
                info = r.json()
                counts = await asyncio.gather(*[client.get(f"https://friends.roblox.com/v1/users/{uid}/{k}/count")
                                                for k in ("friends", "followers", "followings")], return_exceptions=True)
                thumb = await client.get("https://thumbnails.roblox.com/v1/users/avatar",
                                         params={"userIds": uid, "size": "420x420", "format": "Png"})
        except (httpx.HTTPError, ValueError):
            return await interaction.followup.send(tr("רובלוקס לא עונה כרגע. נסה שוב עוד מעט.", lang))

        def count(i):
            c = counts[i]
            return f"{c.json().get('count', 0):,}" if isinstance(c, httpx.Response) and c.status_code == 200 else "—"

        title = info.get("displayName") or info.get("name")
        embed = discord.Embed(title=f"{title} (@{info.get('name')})" + (" ✔️" if info.get("hasVerifiedBadge") else ""),
                              url=f"https://www.roblox.com/users/{uid}/profile",
                              description=(info.get("description") or "")[:500] or None, color=themed(lang, COLOR_INFO))
        embed.add_field(name=tr("מספר משתמש", lang), value=f"`{uid}`")
        created = (info.get("created") or "")[:10]
        if created:
            embed.add_field(name=tr("נוצר", lang), value=created)
        embed.add_field(name=tr("חברים", lang), value=count(0))
        embed.add_field(name=tr("עוקבים", lang), value=count(1))
        embed.add_field(name=tr("נעקבים", lang), value=count(2))
        if info.get("isBanned"):
            embed.add_field(name=tr("מצב", lang), value=tr("חסום ברובלוקס", lang))
        try:
            image = (thumb.json().get("data") or [{}])[0].get("imageUrl")
        except ValueError:
            image = None
        if image:
            embed.set_thumbnail(url=image)
        embed.set_footer(text="Roblox")
        await interaction.followup.send(embed=embed)

    async def _roblox_user_id(self, client: httpx.AsyncClient, kind: str, value: str) -> str | None:
        """An ID, a username or a display name -> the user's ID (None if nobody matches)."""
        if kind == "id":
            r = await client.get(f"https://users.roblox.com/v1/users/{value}")
            if r.status_code == 200:
                return value
        r = await client.post("https://users.roblox.com/v1/usernames/users",
                              json={"usernames": [value], "excludeBannedUsers": False})
        found = (r.json().get("data") or []) if r.status_code == 200 else []
        if found:
            return str(found[0]["id"])
        if len(value) < 3:
            return None
        r = await client.get("https://users.roblox.com/v1/users/search", params={"keyword": value, "limit": 10})
        rows = (r.json().get("data") or []) if r.status_code == 200 else []
        exact = [u for u in rows if value.lower() in ((u.get("displayName") or "").lower(), (u.get("name") or "").lower())]
        pick = (exact or rows or [None])[0]
        return str(pick["id"]) if pick else None

    # ---- /pokedex
    @app_commands.command(name="pokedex", description=N_("מידע על פוקימון מהפוקידקס"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(pokemon=N_("שם הפוקימון (באנגלית) או המספר שלו בפוקידקס"))
    async def pokedex(self, interaction: discord.Interaction, pokemon: app_commands.Range[str, 1, 40]):
        if not await self._allowed(interaction, "pokedex"):
            return
        lang = self._lang(interaction)
        query = pokemon_slug(pokemon)
        await interaction.response.defer(thinking=True)
        try:
            async with httpx.AsyncClient(timeout=15, headers=HTTP_HEADERS) as client:
                found = await self._find_pokemon(client, query)
                if not found:
                    return await interaction.followup.send(tr("לא נמצא פוקימון בשם או במספר {name}.", lang, name=pokemon))
                p, species = found
        except (httpx.HTTPError, ValueError):
            return await interaction.followup.send(tr("שירות הפוקידקס לא עונה כרגע. נסה שוב עוד מעט.", lang))

        def english(entries, key):
            return next((e[key] for e in entries or [] if e.get("language", {}).get("name") == "en"), "")

        number = species.get("id") or p["id"]
        name = english(species.get("names"), "name") or p["name"].title()
        flavor = " ".join(english(species.get("flavor_text_entries"), "flavor_text").split())
        embed = discord.Embed(title=f"#{number:04d} {name}", url=f"https://www.pokemon.com/us/pokedex/{p['species']['name']}",
                              description=flavor[:400] or None, color=themed(lang, COLOR_INFO))
        genus = english(species.get("genera"), "genus")
        if genus:
            embed.set_author(name=genus)
        embed.add_field(name=tr("סוג", lang), value=" / ".join(t["type"]["name"].title() for t in p["types"]))
        embed.add_field(name=tr("גובה", lang), value=f"{p['height'] / 10:g} m")
        embed.add_field(name=tr("משקל", lang), value=f"{p['weight'] / 10:g} kg")
        embed.add_field(name=tr("יכולות", lang), value=", ".join(
            a["ability"]["name"].replace("-", " ").title() + (" *" if a.get("is_hidden") else "") for a in p["abilities"]))
        stat_names = {"hp": "HP", "attack": "Attack", "defense": "Defense", "special-attack": "Sp. Atk",
                      "special-defense": "Sp. Def", "speed": "Speed"}
        lines = []
        for st in p["stats"]:
            value = st["base_stat"]
            bar = "█" * round(value / 15) + "░" * max(0, 17 - round(value / 15))
            lines.append(f"`{stat_names.get(st['stat']['name'], st['stat']['name']):<8} {value:>3} {bar[:17]}`")
        total = sum(st["base_stat"] for st in p["stats"])
        embed.add_field(name=tr("נתוני בסיס (סך הכל {total})", lang, total=total), value="\n".join(lines), inline=False)
        # the picture from the official Pokédex at pokemon.com (numbers up to 1025 have one)
        art = p.get("sprites", {}).get("other", {}).get("official-artwork", {}).get("front_default")
        embed.set_image(url=f"https://assets.pokemon.com/assets/cms2/img/pokedex/full/{number:03d}.png"
                        if number <= 1025 else art)
        embed.set_footer(text="pokemon.com · PokéAPI")
        await interaction.followup.send(embed=embed)

    async def _find_pokemon(self, client: httpx.AsyncClient, query: str):
        """A name or number -> (pokemon, species). Pokémon with several forms (Deoxys, Giratina, Mimikyu...) are
        looked up through their species, which points to the default form."""
        species = None
        r = await client.get(f"https://pokeapi.co/api/v2/pokemon/{query}")
        if r.status_code != 200:
            s = await client.get(f"https://pokeapi.co/api/v2/pokemon-species/{query}")
            if s.status_code != 200:
                close = [n for _, n in self.pokemon_names if n.startswith(query)] if query and not query.isdigit() else []
                if len(close) != 1:
                    return None
                s = await client.get(f"https://pokeapi.co/api/v2/pokemon-species/{close[0]}")
                if s.status_code != 200:
                    return None
            species = s.json()
            default = next((v["pokemon"]["name"] for v in species.get("varieties", []) if v.get("is_default")), None)
            if not default:
                return None
            r = await client.get(f"https://pokeapi.co/api/v2/pokemon/{default}")
            if r.status_code != 200:
                return None
        p = r.json()
        if species is None:
            s = await client.get(p["species"]["url"])
            species = s.json() if s.status_code == 200 else {}
        return p, species

    @pokedex.autocomplete("pokemon")
    async def pokedex_autocomplete(self, interaction: discord.Interaction, current: str):
        q = pokemon_slug(current) if current else ""
        if q.isdigit():
            rows = [(n, name) for n, name in self.pokemon_names if str(n).startswith(q)]
        else:
            starts = [(n, name) for n, name in self.pokemon_names if name.startswith(q)]
            rows = starts + [(n, name) for n, name in self.pokemon_names if q in name and (n, name) not in starts]
        return [app_commands.Choice(name=f"#{n:04d} {name.replace('-', ' ').title()}", value=name) for n, name in rows[:25]]

    # ---- /clear
    @app_commands.command(name="clear", description=N_("מחיקת ההודעות האחרונות של הבוט בערוץ הזה"))
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=False)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(amount=N_("כמה הודעות של הבוט למחוק (1 עד 100)"))
    async def clear(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        if not await self._allowed(interaction, "clear"):
            return
        lang = self._lang(interaction)
        channel = interaction.channel
        if interaction.guild is None:
            channel = interaction.user.dm_channel or await interaction.user.create_dm()
        await interaction.response.defer(ephemeral=True, thinking=True)
        me = self.bot.user
        perms = channel.permissions_for(interaction.guild.me) if interaction.guild else None
        if perms is not None and not perms.read_message_history:
            return await interaction.followup.send(tr("אין לבוט הרשאה לקרוא את היסטוריית הערוץ הזה.", lang), ephemeral=True)
        mine = []
        try:
            async for message in channel.history(limit=min(1000, amount * 25)):
                if message.author.id == me.id:
                    mine.append(message)
                    if len(mine) >= amount:
                        break
        except discord.HTTPException:
            return await interaction.followup.send(tr("אין לבוט הרשאה לקרוא את היסטוריית הערוץ הזה.", lang), ephemeral=True)
        deleted = 0
        recent = [m for m in mine if (discord.utils.utcnow() - m.created_at).days < 13]
        if perms is not None and perms.manage_messages and len(recent) > 1:
            for i in range(0, len(recent), 100):  # fast: many at once
                try:
                    await channel.delete_messages(recent[i:i + 100])
                    deleted += len(recent[i:i + 100])
                except discord.HTTPException:
                    pass
            mine = [m for m in mine if m not in recent]
        for message in mine:  # one by one (always allowed for the bot's own messages)
            try:
                await message.delete()
                deleted += 1
            except discord.HTTPException:
                pass
        await interaction.followup.send(tr("נמחקו {count} הודעות של הבוט.", lang, count=deleted), ephemeral=True)

    # ---- /morse and /binary
    @app_commands.command(name="morse", description=N_("תרגום טקסט לקוד מורס (או ממורס לטקסט)"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(text=N_("טקסט לתרגום, או קוד מורס לפענוח"))
    async def morse(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 500]):
        if not await self._allowed(interaction, "morse"):
            return
        lang = self._lang(interaction)
        if is_morse(text):
            result, title = from_morse(text), tr("ממורס לטקסט", lang)
            note = ""
        else:
            result, skipped = to_morse(text)
            title = tr("מטקסט למורס", lang)
            note = tr("לתווים האלה אין קוד מורס והם דולגו: {chars}", lang, chars=" ".join(skipped)) if skipped else ""
        if not result:
            return await interaction.response.send_message(tr("אין כאן אותיות שאפשר לתרגם למורס (רק אנגלית, מספרים וסימנים).", lang),
                                                           ephemeral=True)
        embed = discord.Embed(title=title, description=f"```\n{result[:3900]}\n```", color=themed(lang, COLOR_INFO))
        if note:
            embed.set_footer(text=note[:200])
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="binary", description=N_("תרגום טקסט לבינארי (או מבינארי לטקסט)"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(text=N_("טקסט לתרגום, או בינארי (0 ו-1) לפענוח"))
    async def binary(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 400]):
        if not await self._allowed(interaction, "binary"):
            return
        lang = self._lang(interaction)
        if is_binary(text):
            result, title = from_binary(text), tr("מבינארי לטקסט", lang)
        else:
            result, title = to_binary(text), tr("מטקסט לבינארי", lang)
        embed = discord.Embed(title=title, description=f"```\n{result[:3900]}\n```", color=themed(lang, COLOR_INFO))
        if len(result) > 3900:
            embed.set_footer(text=tr("התוצאה ארוכה מדי ונחתכה.", lang))
        await interaction.response.send_message(embed=embed)

    # ---- /qr
    @app_commands.command(name="qr", description=N_("יצירת קוד QR מקישור, טקסט, Wi-Fi ועוד"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(content=N_("מה להכניס לקוד: קישור, טקסט, שם רשת Wi-Fi, אימייל או מספר טלפון"),
                           kind=N_("סוג התוכן (ברירת מחדל: מזהה לבד)"),
                           extra=N_("סיסמת Wi-Fi, נושא לאימייל או טקסט ל-SMS (לא חובה)"),
                           security=N_("אבטחת ה-Wi-Fi (ברירת מחדל WPA)"))
    @app_commands.choices(kind=[app_commands.Choice(name=label, value=key) for key, label in QR_KINDS],
                          security=[app_commands.Choice(name="WPA / WPA2 / WPA3", value="WPA"),
                                    app_commands.Choice(name="WEP", value="WEP"),
                                    app_commands.Choice(name=N_("בלי סיסמה"), value="nopass")])
    async def qr_code(self, interaction: discord.Interaction, content: app_commands.Range[str, 1, 200],
                      kind: str | None = None, extra: app_commands.Range[str, 0, 100] | None = None,
                      security: str | None = None):
        if not await self._allowed(interaction, "qr"):
            return
        lang = self._lang(interaction)
        if kind is None:
            low = content.strip().lower()
            kind = "url" if low.startswith(("http://", "https://", "www.")) else \
                "email" if "@" in low and " " not in low and "." in low.split("@")[-1] else "text"
        payload = qr_payload(kind, content, extra or "", security or "WPA")
        try:
            png = qr.to_png(qr.encode(payload), scale=10)
        except ValueError:
            return await interaction.response.send_message(
                tr("התוכן ארוך מדי לקוד QR (עד {max} תווים באנגלית).", lang, max=qr.capacity_bytes()), ephemeral=True)
        embed = discord.Embed(title=tr("קוד QR", lang), color=themed(lang, COLOR_INFO))
        shown = content if kind != "wifi" else tr("רשת Wi-Fi: {name}", lang, name=content)
        embed.description = discord.utils.escape_markdown(shown[:300])
        embed.set_image(url="attachment://qr.png")
        await interaction.response.send_message(embed=embed, file=discord.File(io.BytesIO(png), "qr.png"))

    # ---- /gif
    @app_commands.command(name="gif", description=N_("הפיכת סרטון או תמונה ל-GIF"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(video=N_("הקובץ (בלי קובץ: הסרטון או התמונה האחרונים בערוץ)"),
                           width=N_("רוחב ה-GIF בפיקסלים (ברירת מחדל 480)"),
                           fps=N_("פריימים בשנייה (ברירת מחדל 12)"), seconds=N_("כמה שניות מתחילת הסרטון (ברירת מחדל 8)"))
    async def gif(self, interaction: discord.Interaction, video: discord.Attachment | None = None,
                  width: app_commands.Range[int, 120, 800] = 480, fps: app_commands.Range[int, 5, 30] = 12,
                  seconds: app_commands.Range[int, 1, 20] = 8):
        if not await self._allowed(interaction, "gif"):
            return
        lang = self._lang(interaction)
        if video is None:  # no file: use the newest video or picture in this channel
            video = await self._recent_media(interaction.channel)
            if video is None:
                return await interaction.response.send_message(
                    tr("לא מצאתי סרטון או תמונה בהודעות האחרונות. צרף קובץ, או לחץ קליק ימני על ההודעה > Apps > Make GIF.", lang),
                    ephemeral=True)
        await self._gif_reply(interaction, lang, video, width, fps, seconds)

    async def gif_from_message(self, interaction: discord.Interaction, message: discord.Message):
        """Right click on a message > Apps > Make GIF."""
        if not await self._allowed(interaction, "gif"):
            return
        lang = self._lang(interaction)
        media = media_of(message)
        if media is None:
            return await interaction.response.send_message(tr("בהודעה הזו אין סרטון או תמונה.", lang), ephemeral=True)
        await self._gif_reply(interaction, lang, media, 480, 12, 8)

    async def _recent_media(self, channel):
        try:
            async for message in channel.history(limit=30):
                media = media_of(message)
                if media is not None:
                    return media
        except (discord.HTTPException, AttributeError):
            return None
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Replying '!gif' (or '/gif' sent as text) to a message with a video or picture turns it into a GIF."""
        if message.author.bot or message.reference is None or message.content.strip().lower() not in GIF_REPLY_WORDS:
            return
        if message.guild is not None:
            cfg = self.bot.cfg(message.guild, "tools")
            if not cfg or not cfg.get("gif", True):
                return
        lang = self.bot.lang(message.guild) if message.guild else ui_lang()
        target = message.reference.resolved
        if not isinstance(target, discord.Message):
            try:
                target = await message.channel.fetch_message(message.reference.message_id)
            except discord.HTTPException:
                return
        media = media_of(target)
        if media is None:
            return await message.reply(tr("בהודעה הזו אין סרטון או תמונה.", lang), mention_author=False)
        async with message.channel.typing():
            result = await self._make_gif(media, 480, 12, 8)
        if isinstance(result, str):
            return await message.reply(tr(result, lang), mention_author=False)
        path, name, tmp = result
        try:
            await message.reply(file=discord.File(path, name), mention_author=False)
        finally:
            tmp.cleanup()

    async def _gif_reply(self, interaction, lang, media, width, fps, seconds) -> None:
        if media.size > MAX_VIDEO_BYTES:
            return await interaction.response.send_message(tr("הסרטון גדול מדי (עד 50MB).", lang), ephemeral=True)
        if not find_ffmpeg():
            log.warning(tr("ffmpeg לא נמצא: /gif צריך אותו. מתקינים עם הפקודה winget install ffmpeg"))
            return await interaction.response.send_message(
                tr("כדי להפוך סרטונים ל-GIF, מי שמריץ את הבוט צריך להתקין ffmpeg (בחלון PowerShell: winget install ffmpeg) ולהפעיל את הבוט מחדש.", lang),
                ephemeral=True)
        await interaction.response.defer(thinking=True)
        result = await self._make_gif(media, width, fps, seconds)
        if isinstance(result, str):
            return await interaction.followup.send(tr(result, lang))
        path, name, tmp = result
        try:
            await interaction.followup.send(file=discord.File(path, name))
        finally:
            tmp.cleanup()

    async def _make_gif(self, media, width: int, fps: int, seconds: int):
        """Video or picture -> (gif path, file name, temp dir to clean up), or an error message (Hebrew source text)."""
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return N_("כדי להפוך סרטונים ל-GIF, מי שמריץ את הבוט צריך להתקין ffmpeg (בחלון PowerShell: winget install ffmpeg) ולהפעיל את הבוט מחדש.")
        if media.size > MAX_VIDEO_BYTES:
            return N_("הסרטון גדול מדי (עד 50MB).")
        tmp = tempfile.TemporaryDirectory(prefix="botd-gif-")
        suffix = Path(media.filename).suffix.lower() or ".bin"
        src, dst = Path(tmp.name) / f"in{suffix}", Path(tmp.name) / "out.gif"
        try:
            await media.save(src)
            is_picture = (media.content_type or "").startswith("image/") or suffix in (".png", ".jpg", ".jpeg", ".webp")
            if is_picture and suffix != ".gif" and not (media.content_type or "").startswith("image/gif"):
                out = await images.convert(ffmpeg, src, "gif", Path(tmp.name))
                dst = out
            else:
                size_try = width
                for _ in range(3):  # if the GIF is too big for Discord, make it smaller and try again
                    await video_to_gif(ffmpeg, src, dst, size_try, fps, seconds)
                    if dst.stat().st_size <= MAX_GIF_BYTES:
                        break
                    size_try = max(120, int(size_try * 0.7))
            if dst.stat().st_size > MAX_GIF_BYTES:
                tmp.cleanup()
                return N_("ה-GIF יצא גדול מדי לדיסקורד. נסה פחות שניות או רוחב קטן יותר.")
        except (RuntimeError, images.ImageError, discord.HTTPException) as exc:
            log.warning("gif conversion failed: %s", exc)
            tmp.cleanup()
            return N_("לא הצלחתי להמיר את הסרטון ל-GIF.")
        name = re.sub(r"[^\w.-]", "_", Path(media.filename).stem)[:60] or "video"
        return dst, f"{name}.gif", tmp
