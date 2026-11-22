"""Tests for ChatGPT/Grok providers, per-server customization, slash command renames, app look and the Console."""
import asyncio
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from botd import customize
from botd.bot import BotManager, ManagedBot
from botd.i18n import Lang, tr
from botd.store import Store

GID = "111111111111111111"
USER = "123456789012345678"
REQUESTS, NEXT = [], []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        data = json.dumps(NEXT.pop(0)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def make_env():
    store = Store(Path(tempfile.mkdtemp()) / "t.db")
    store.upsert_guild(GID, "Test")
    bot = ManagedBot(BotManager(store), {"members": True, "message_content": True})
    return store, bot


def test_custom_texts_and_colors():
    from botd.modules.common import COLOR_DANGER, emoji, themed

    key = "⏰ אזכיר לך {when}."
    assert tr(key, Lang("en"), when="soon") == "⏰ I'll remind you soon."
    assert tr(key, Lang("en", {key: "Got it, ping you {when}!"}), when="soon") == "Got it, ping you soon!"
    assert tr(key, Lang("he", {key: "broken {nope}"}), when="x") == "⏰ אזכיר לך x."  # broken custom text falls back
    lang = Lang("en", colors={"danger": "#00FF00"}, emojis={"vote_up": "🔥"})
    assert themed(lang, COLOR_DANGER) == 0x00FF00 and themed(lang, 0x123456) == 0x123456 and themed("en", COLOR_DANGER) == COLOR_DANGER
    assert emoji(lang, "vote_up") == "🔥" and emoji(lang, "vote_down") == "👎"

    store, bot = make_env()
    store.set_module(GID, "reminders", True)
    store.set_guild_lang(GID, "en")
    store.set_config(GID, "_custom", customize.validate_custom({"texts": {key: "Ok! {when}"}, "colors": {"brand": "#112233"}}))
    guild = SimpleNamespace(id=int(GID))
    cfg = bot.cfg(guild, "reminders")
    assert cfg["_lang"] == "en" and tr(key, cfg["_lang"], when="later") == "Ok! later"
    assert bot.lang(guild).colors == {"brand": "#112233"}

    texts = customize.text_catalog("en")
    assert len(texts) > 250 and any(t["key"] == key and t["default"] == "⏰ I'll remind you {when}." for t in texts)
    assert not any(t["key"] == "יצירת סקר" for t in texts)  # slash command descriptions live on the Commands page
    for bad in ({"texts": {"not a real text": "x"}}, {"texts": {key: "hi {user}"}}, {"colors": {"brand": "red"}},
                {"colors": {"nope": "#000000"}}, {"emojis": {"vote_up": "x" * 80}}):
        try:
            customize.validate_custom(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert customize.validate_custom({"texts": {key: "   "}})["texts"] == {}  # empty = default
    store.close()
    print("ok custom bot texts, colors and emojis per server")


def test_commands_and_look():
    known = {"rank", "poll", "levels"}
    assert customize.validate_commands({"rank": {"name": "level", "description": "Your level"}, "poll": {"enabled": False}}, known) == \
        {"rank": {"name": "level", "description": "Your level"}, "poll": {"enabled": False}}
    for bad in ({"rank": {"name": "Bad Name"}}, {"rank": {"name": "levels"}}, {"rank": {"description": "x" * 101}}):
        try:
            customize.validate_commands(bad, known)
            raise AssertionError(bad)
        except ValueError:
            pass

    async def renamed():
        from botd.modules import COGS

        store, bot = make_env()
        store.set_kv("command_custom", {"rank": {"name": "level", "description": "Show your level"}, "poll": {"enabled": False}})
        for c in COGS:
            await bot.add_cog(c(bot))
        bot.apply_command_custom()
        bot._localize_commands()
        names = {c.name for c in bot.tree.get_commands()}
        assert "level" in names and "rank" not in names and "poll" not in names, names
        level = bot.tree.get_command("level")
        assert level.description == "Show your level" and level.to_dict(bot.tree)["name"] == "level"
        for name in list(bot.cogs):
            await bot.remove_cog(name)
        store.close()
    asyncio.run(renamed())

    look = customize.validate_appearance({"theme": "light", "accent": "#FF0000", "font_scale": 500, "radius": -3, "custom_css": ".x{color:red}"})
    assert look["theme"] == "light" and look["font_scale"] == 140 and look["radius"] == 0 and look["custom_css"] == ".x{color:red}"
    for css in ("@import url(x.css);", "body{background:url('https://evil/x.png')}", "</style><script>"):
        try:
            customize.validate_appearance({"custom_css": css})
            raise AssertionError(css)
        except ValueError:
            pass
    store, _ = make_env()
    assert store.update_settings({"appearance": {"theme": "light"}})["appearance"]["theme"] == "light"
    store.close()
    print("ok slash command renames/descriptions/off, app look validation")


def test_console():
    from botd.console import run_command

    store, _ = make_env()
    manager = SimpleNamespace(bot=None, state="stopped", status=lambda: {"state": "stopped", "user": None, "guild_count": None,
                                                                          "latency_ms": None, "intents": None})

    async def go():
        assert len(await run_command(manager, store, "help")) >= 9
        assert (await run_command(manager, store, "status"))[0].endswith("stopped")
        assert "start" in (await run_command(manager, store, "say 123 hi"))[0]  # offline -> tells how to start
        out = await run_command(manager, store, "module Test levels on")
        assert store.module_enabled(GID, "levels") and "levels" in out[0]
        assert "levels" in (await run_command(manager, store, "modules Test"))[0]
        assert "nope" in (await run_command(manager, store, "module Test nope on"))[0]
        assert "xyz" in (await run_command(manager, store, "xyz"))[0]
        # online: say sends to the channel
        chan = MagicMock()
        chan.name, chan.guild, chan.send = "general", SimpleNamespace(name="Test"), AsyncMock()
        bot = MagicMock()
        bot.get_channel = lambda cid: chan if cid == 555 else None
        bot.guilds = []
        manager.bot, manager.state = bot, "connected"
        out = await run_command(manager, store, 'say 555 "hello there" friends')
        assert chan.send.await_args.args[0] == '"hello there" friends' and "general" in out[0]
    asyncio.run(go())
    store.close()
    print("ok console commands: help, status, module on/off, say")


def test_openai_and_grok():
    from botd.modules import ai_providers
    from botd.modules.claude_chat import ClaudeChat

    msgs = ai_providers.to_openai_messages("SYS", [
        {"role": "user", "content": "who talks most?"},
        {"role": "assistant", "content": [SimpleNamespace(type="tool_use", id="c1", name="top_members", input={"limit": 3})]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "Dana: 5"}]},
    ])
    assert msgs[0] == {"role": "system", "content": "SYS"} and msgs[2]["tool_calls"][0]["function"]["name"] == "top_members"
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "Dana: 5"}
    r = ai_providers.from_openai_response({"model": "gpt-6-luna", "choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"id": "x", "function": {"name": "add_birthday", "arguments": '{"person":"John","date":"24/12"}'}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4}}, "gpt-6-luna")
    assert r.stop_reason == "tool_use" and r.content[0].input == {"person": "John", "date": "24/12"} and r.usage.input_tokens == 10
    assert ai_providers.model_for("openai", {"openai_model": "sol"}) == "gpt-6.2-sol"
    assert ai_providers.model_for("grok", {"grok_model": "grok-4.7"}) == "grok-4.7"
    assert ai_providers.model_for("openai", {"custom_model": "gpt-7-future"}) == "gpt-7-future"

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/v1"
    for p in ("openai", "grok"):
        ai_providers.PROVIDERS[p]["base"] = base
    from botd import secrets_store
    secrets_store.get = lambda name: {"openai_key": "sk-openai-test", "xai_key": "xai-test"}.get(name)

    store, bot = make_env()
    store.count_message(GID, "77", "Dana")

    async def scenario():
        cog = ClaudeChat(bot)
        guild = MagicMock(spec=discord.Guild)
        guild.id, guild.name, guild.channels, guild.roles, guild.members = int(GID), "Test", [], [], []
        author = MagicMock(spec=discord.Member)
        author.id, author.bot = int(USER), False
        msg = MagicMock(spec=discord.Message)
        msg.guild, msg.author, msg.content = guild, author, "who talks most?"
        msg.channel = MagicMock(id=9)
        msg.channel.typing = lambda: SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))

        class Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False
        msg.channel.typing = lambda: Typing()
        msg.reply = AsyncMock()
        cfg = {"system_prompt": "SYS", "effort": "low", "history_messages": 5, "reply_without_mention": False,
               "cooldown_seconds": 0, "daily_limit": 0, "provider": "openai", "openai_model": "luna", "_lang": "en"}
        NEXT.extend([
            {"model": "gpt-6-luna", "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "top_members", "arguments": '{"limit": 5}'}}]}}],
             "usage": {"prompt_tokens": 30, "completion_tokens": 5}},
            {"model": "gpt-6-luna", "choices": [{"finish_reason": "stop", "message": {"content": "Dana talks the most."}}],
             "usage": {"prompt_tokens": 50, "completion_tokens": 8}},
        ])
        await cog._answer(msg, "who talks most?", cfg)
        first, second = REQUESTS[-2], REQUESTS[-1]
        assert first["path"] == "/v1/chat/completions" and first["auth"] == "Bearer sk-openai-test"
        assert first["body"]["model"] == "gpt-6-luna" and first["body"]["max_completion_tokens"] == 4000
        assert first["body"]["messages"][0]["role"] == "system" and "same language" in first["body"]["messages"][0]["content"]
        tool = next(t for t in first["body"]["tools"] if t["function"]["name"] == "top_members")
        assert tool["type"] == "function" and tool["function"]["strict"] is True
        assert second["body"]["messages"][-1]["role"] == "tool" and "Dana" in second["body"]["messages"][-1]["content"]
        assert msg.reply.await_args.args[0] == "Dana talks the most."

        cfg.update(provider="grok", grok_model="grok-4.7")
        NEXT.append({"model": "grok-4.7", "choices": [{"finish_reason": "stop", "message": {"content": "Hi from Grok"}}],
                     "usage": {"prompt_tokens": 9, "completion_tokens": 3}})
        await cog._answer(msg, "hi", cfg)
        g = REQUESTS[-1]
        assert g["auth"] == "Bearer xai-test" and g["body"]["model"] == "grok-4.7" and "max_tokens" in g["body"]
        assert "strict" not in json.dumps(g["body"]["tools"])
        assert msg.reply.await_args.args[0] == "Hi from Grok"
        from botd.modules.claude_chat import usage_summary
        day = usage_summary(store)["days"][0]
        assert set(day["by_model"]) == {"gpt-6-luna", "grok-4.7"} and day["est_cost_usd"] > 0
        await cog.cog_unload()
    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        store.close()
    print("ok ChatGPT and Grok: same tools, read-tool loop, keys, models, cost tracking")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
