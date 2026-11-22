"""Translation tests: full coverage, placeholders, per-server bot language, catalog and slash command texts."""
import asyncio
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discord

from botd import catalog, i18n
from botd.bot import BotManager, ManagedBot
from botd.store import Store

GID = "111111111111111111"


def test_coverage_tool():
    out = subprocess.run([sys.executable, str(ROOT / "tools/i18n_check.py"), "--strict"], capture_output=True, text=True,
                         encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    first = out.stdout.splitlines()[0]
    assert '"catalog": 0, "python": 0' in first, out.stdout[:2000]
    assert "UNWRAPPED botd" not in out.stdout, out.stdout[:2000]
    print("ok coverage:", first)


def test_ui_texts_all_translated():
    """Every Hebrew text in the UI code (labels in arrays and dropdowns too) and in list defaults has a translation."""
    out = subprocess.run([sys.executable, str(ROOT / "tools/ui_hebrew_audit.py"), "--strict"], capture_output=True, text=True,
                         encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0, out.stdout[-2000:]
    print("ok", out.stdout.strip().splitlines()[-1])


def test_placeholders_and_completeness():
    ph = re.compile(r"\{\w+\}|%[sd+]")
    for key, row in i18n.table().items():
        assert len(row) == 3 and all(row), key
        for value in row:
            assert sorted(ph.findall(key)) == sorted(ph.findall(value)), (key, value)
    print("ok", len(i18n.table()), "entries, all with 3 translations and matching placeholders")


def test_tr_and_catalog():
    assert i18n.tr("סיבה: {reason}", "en", reason="spam") == "Reason: spam"
    assert i18n.tr("סיבה: {reason}", "he", reason="spam") == "סיבה: spam"
    assert i18n.tr("לא קיים בכלל", "ru") == "לא קיים בכלל"  # unknown -> source text
    cat = i18n.translate_catalog(catalog.catalog_json(), "ar")
    levels = next(m for m in cat["modules"] if m["key"] == "levels")
    assert levels["label"] == "المستويات" and not re.search(r"[֐-׿]", levels["description"])
    assert catalog.module_defaults("welcome", "en")["welcome_message"].startswith("Welcome {user}")
    assert catalog.module_defaults("welcome", "he")["welcome_message"].startswith("ברוך הבא")
    i18n.set_ui_lang("ru")
    try:
        catalog.validate_config("automod", {"spam": {"on": True, "action": "warn", "count": 1}})
        raise AssertionError("should fail")
    except ValueError as exc:
        assert "от 2 до 30" in str(exc), str(exc)  # validation errors follow the UI language
    finally:
        i18n.set_ui_lang("he")
    print("ok tr, catalog translation, language-aware defaults and errors")


async def bot_language():
    store = Store(Path(tempfile.mkdtemp()) / "t.db")
    store.upsert_guild(GID, "Test")
    bot = ManagedBot(BotManager(store), {"members": True, "message_content": True})
    g = MagicMock(spec=discord.Guild)
    g.id = int(GID)
    assert bot.lang(g) == "he"
    store.set_guild_lang(GID, "en")
    store.set_module(GID, "levels", True)
    assert bot.lang(g) == "en" and bot.cfg(g, "levels")["_lang"] == "en"
    assert bot.cfg(g, "levels")["announce_message"].startswith("🎉 Well done")

    from botd.modules.moderation import Moderation
    mod = Moderation(bot)
    inter = MagicMock()
    inter.guild = g
    inter.user = MagicMock(spec=discord.Member)
    assert await mod._guard(inter, "kick_members") == "Moderation commands are off in this server. You can turn them on in the app."

    from botd.modules import Levels, Warnings  # noqa: F401
    from botd.modules import COGS
    for c in COGS:
        await bot.add_cog(c(bot))
    i18n.set_ui_lang("en")
    bot._localize_commands()
    kick = next(c for c in bot.tree.get_commands() if c.name == "kick")
    assert kick.description == "Kick a member (with confirmation)", kick.description
    assert kick._params["member"].description == "Who to kick"
    rps = next(c for c in bot.tree.get_commands() if c.name == "rps")
    assert [ch.name for ch in rps._params["choice"].choices][0] == "🪨 Rock"
    i18n.set_ui_lang("he")
    bot._localize_commands()
    assert kick.description == "הרחקת חבר מהשרת (עם אישור)"
    print("ok per-server bot language, translated defaults and slash command texts")
    await bot.close()
    store.close()


if __name__ == "__main__":
    test_coverage_tool()
    test_placeholders_and_completeness()
    test_tr_and_catalog()
    asyncio.run(bot_language())
