"""Info and tool commands: /roblox-user, /pokedex, /morse, /binary, /qr, /gif, /opacity, /file-format, /seed.

They work in servers (when the module is on there) and also as a user app ("Add App"): installed to a Discord
account, they can be used in DMs and in any server, even where the bot itself is not a member.
"""

from ..i18n import N_, tr, ui_lang
import asyncio
import io
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

from .. import images, qr
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


# ---- the cog ------------------------------------------------------------------------------------------------------
QR_KINDS = [("url", N_("קישור")), ("text", N_("טקסט")), ("wifi", N_("Wi-Fi")), ("email", N_("אימייל")),
            ("phone", N_("טלפון")), ("sms", N_("SMS"))]


class Tools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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

    # ---- /roblox-user
    @app_commands.command(name="roblox-user", description=N_("מידע על משתמש רובלוקס"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(user=N_("שם משתמש או מספר (ID) ברובלוקס"))
    async def roblox_user(self, interaction: discord.Interaction, user: app_commands.Range[str, 1, 40]):
        if not await self._allowed(interaction, "roblox"):
            return
        lang = self._lang(interaction)
        await interaction.response.defer(thinking=True)
        try:
            async with httpx.AsyncClient(timeout=15, headers=HTTP_HEADERS) as client:
                uid = user.strip()
                if not uid.isdigit():
                    r = await client.post("https://users.roblox.com/v1/usernames/users",
                                          json={"usernames": [uid], "excludeBannedUsers": False})
                    found = (r.json().get("data") or []) if r.status_code == 200 else []
                    if not found:
                        return await interaction.followup.send(tr("לא נמצא משתמש רובלוקס בשם {name}.", lang, name=user))
                    uid = str(found[0]["id"])
                r = await client.get(f"https://users.roblox.com/v1/users/{uid}")
                if r.status_code != 200:
                    return await interaction.followup.send(tr("לא נמצא משתמש רובלוקס בשם {name}.", lang, name=user))
                info = r.json()
                counts = await asyncio.gather(*[client.get(f"https://friends.roblox.com/v1/users/{uid}/{k}/count")
                                                for k in ("friends", "followers", "followings")], return_exceptions=True)
                thumb = await client.get("https://thumbnails.roblox.com/v1/users/avatar",
                                         params={"userIds": uid, "size": "420x420", "format": "Png"})
        except httpx.HTTPError:
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

    # ---- /pokedex
    @app_commands.command(name="pokedex", description=N_("מידע על פוקימון מהפוקידקס"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(pokemon=N_("מספר בפוקידקס או שם הפוקימון"))
    async def pokedex(self, interaction: discord.Interaction, pokemon: app_commands.Range[str, 1, 40]):
        if not await self._allowed(interaction, "pokedex"):
            return
        lang = self._lang(interaction)
        query = pokemon.strip().lower().lstrip("#").replace(" ", "-")
        query = str(int(query)) if query.isdigit() else query
        await interaction.response.defer(thinking=True)
        try:
            async with httpx.AsyncClient(timeout=15, headers=HTTP_HEADERS) as client:
                r = await client.get(f"https://pokeapi.co/api/v2/pokemon/{query}")
                if r.status_code != 200:
                    return await interaction.followup.send(tr("לא נמצא פוקימון בשם או במספר {name}.", lang, name=pokemon))
                p = r.json()
                s = await client.get(p["species"]["url"])
                species = s.json() if s.status_code == 200 else {}
        except httpx.HTTPError:
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
    @app_commands.command(name="gif", description=N_("הפיכת סרטון (MP4 ועוד) ל-GIF"))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(**EVERYWHERE)
    @app_commands.describe(video=N_("קובץ הסרטון"), width=N_("רוחב ה-GIF בפיקסלים (ברירת מחדל 480)"),
                           fps=N_("פריימים בשנייה (ברירת מחדל 12)"), seconds=N_("כמה שניות מתחילת הסרטון (ברירת מחדל 8)"))
    async def gif(self, interaction: discord.Interaction, video: discord.Attachment,
                  width: app_commands.Range[int, 120, 800] = 480, fps: app_commands.Range[int, 5, 30] = 12,
                  seconds: app_commands.Range[int, 1, 20] = 8):
        if not await self._allowed(interaction, "gif"):
            return
        lang = self._lang(interaction)
        ctype = (video.content_type or "").lower()
        if not ctype.startswith(VIDEO_TYPES) and not video.filename.lower().endswith((".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif")):
            return await interaction.response.send_message(tr("זה לא קובץ וידאו. צרף קובץ MP4, MOV או WEBM.", lang), ephemeral=True)
        if video.size > MAX_VIDEO_BYTES:
            return await interaction.response.send_message(tr("הסרטון גדול מדי (עד 50MB).", lang), ephemeral=True)
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            log.warning(tr("ffmpeg לא נמצא: /gif צריך אותו. מתקינים עם הפקודה winget install ffmpeg"))
            return await interaction.response.send_message(
                tr("כדי להפוך סרטונים ל-GIF, מי שמריץ את הבוט צריך להתקין ffmpeg (בחלון PowerShell: winget install ffmpeg) ולהפעיל את הבוט מחדש.", lang),
                ephemeral=True)
        await interaction.response.defer(thinking=True)
        with tempfile.TemporaryDirectory(prefix="botd-gif-") as tmp:
            src, dst = Path(tmp) / "in.bin", Path(tmp) / "out.gif"
            await video.save(src)
            size_try = width
            for _ in range(3):  # if the GIF is too big for Discord, make it smaller and try again
                try:
                    await video_to_gif(ffmpeg, src, dst, size_try, fps, seconds)
                except RuntimeError as exc:
                    log.warning("gif conversion failed: %s", exc)
                    return await interaction.followup.send(tr("לא הצלחתי להמיר את הסרטון ל-GIF.", lang))
                if dst.stat().st_size <= MAX_GIF_BYTES:
                    break
                size_try = max(120, int(size_try * 0.7))
            else:
                return await interaction.followup.send(tr("ה-GIF יצא גדול מדי לדיסקורד. נסה פחות שניות או רוחב קטן יותר.", lang))
            name = Path(video.filename).stem[:60] or "video"
            await interaction.followup.send(file=discord.File(dst, f"{name}.gif"))
