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
        store.set_kv("command_custom", {"rank": {"name": "mylevel", "description": "Show your level"}, "poll": {"enabled": False},
                                        "levels": {"name": "level"}})  # "level" is taken by /level: that rename is skipped
        for c in COGS:
            await bot.add_cog(c(bot))
        bot.apply_command_custom()
        bot._localize_commands()
        names = {c.name for c in bot.tree.get_commands()}
        assert "mylevel" in names and "rank" not in names and "poll" not in names and "levels" in names, names
        level = bot.tree.get_command("mylevel")
        assert level.description == "Show your level" and level.to_dict(bot.tree)["name"] == "mylevel"
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


def test_twitch_alerts():
    from botd import catalog
    from botd.modules.social_alerts import SocialAlerts, decapi_live, twitch_login

    assert twitch_login("https://www.twitch.tv/Some_Streamer?ref=x") == "some_streamer" and twitch_login("@Name") == "name"
    assert decapi_live("1 hour, 5 minutes, 2 seconds") and not decapi_live("someone is offline") and not decapi_live("")
    # a half-filled row (source without a channel) is an error now, an empty row is still ignored
    try:
        catalog.validate_config("social_alerts", {"feeds": [{"kind": "twitch", "source": "name", "channel_id": ""}]})
        raise AssertionError("half-filled row accepted")
    except ValueError as exc:
        assert "row 1" in str(exc) or "בשורה 1" in str(exc), exc
    assert catalog.validate_config("social_alerts", {"feeds": [{"kind": "twitch", "source": "", "channel_id": ""}]})["feeds"] == []

    store, bot = make_env()
    chan = MagicMock()
    chan.send = AsyncMock()
    guild = MagicMock(spec=discord.Guild)
    guild.id = int(GID)
    guild.get_channel = lambda cid: chan
    feed = {"kind": "twitch", "source": "https://twitch.tv/streamer1", "channel_id": "333333333333333333",
            "message": "📣 {name}: {title}\n{url}", "role_id": ""}
    states = iter([
        {"live": True, "stream_id": "", "title": "old stream", "game": "", "name": "streamer1", "url": "https://twitch.tv/streamer1", "thumb": "", "via": "decapi"},
        {"live": False, "stream_id": "", "title": "", "game": "", "name": "streamer1", "url": "https://twitch.tv/streamer1", "thumb": "", "via": "decapi"},
        {"live": True, "stream_id": "", "title": "Ranked games", "game": "Valorant", "name": "streamer1", "url": "https://twitch.tv/streamer1",
         "thumb": "https://static-cdn.jtvnw.net/previews-ttv/live_user_streamer1-640x360.jpg", "via": "decapi"},
        {"live": True, "stream_id": "", "title": "Ranked games", "game": "Valorant", "name": "streamer1", "url": "https://twitch.tv/streamer1", "thumb": "", "via": "decapi"},
    ])

    async def go():
        cog = SocialAlerts(bot)
        cog.loop.cancel()

        async def fake_status(client, login):
            assert login == "streamer1"
            return next(states)
        cog.twitch_status = fake_status
        await cog.check(None, guild, feed)   # already live when added: no alert for an old stream
        await cog.check(None, guild, feed)   # offline
        assert chan.send.await_count == 0
        await cog.check(None, guild, feed)   # goes live -> one alert with an embed
        assert chan.send.await_count == 1
        sent = chan.send.await_args
        assert "Ranked games" in sent.args[0] and sent.kwargs["embed"].fields[0].value == "Valorant"
        assert sent.kwargs["embed"].image.url.startswith("https://static-cdn.jtvnw.net/previews-ttv/live_user_streamer1")
        await cog.check(None, guild, feed)   # still the same stream: no second alert
        assert chan.send.await_count == 1
        status = store.get_kv(f"feed_status:{GID}:twitch:{feed['source'].lower()}")
        assert status["ok"] and "Ranked games" in status["msg"]

        async def offline(client, login):
            return {"live": False, "stream_id": "", "title": "", "game": "", "name": login, "url": "u", "thumb": "", "via": "decapi"}
        cog.twitch_status = offline
        assert "decapi.me" in await cog.preview(guild, feed)
        await cog.preview(guild, feed, send=True)   # test alert even while offline
        assert chan.send.await_args.args[0].startswith("🧪 ")
    asyncio.run(go())
    store.close()
    print("ok twitch: keyless live detection, one alert per stream, embed with game and preview, test button")


def test_bot_accounts_and_server_profile():
    import base64

    from botd import discord_rest, secrets_store
    from botd.bot import BotFleet

    secrets_store._check("bot_token_a1b2c3")
    try:
        secrets_store._check("bot_token_../evil")
        raise AssertionError("bad secret name accepted")
    except KeyError:
        pass

    store, _ = make_env()
    store.upsert_guild("222222222222222222", "Second")
    store.set_guild_lang(GID, "en")
    fleet = BotFleet(store)
    assert fleet.main.owns(GID)                    # one bot: it runs everything
    extra = fleet.add_account("Second bot")
    assert BotFleet(store).extras.keys() == fleet.extras.keys()  # accounts are remembered

    def fake_bot(guild_ids, account):
        b = MagicMock()
        b.get_guild = lambda gid: SimpleNamespace(id=gid) if str(gid) in guild_ids else None
        b.manager = account
        return b
    fleet.main.bot, fleet.main.state = fake_bot({GID}, fleet.main), "connected"
    extra.bot, extra.state = fake_bot({GID, "222222222222222222"}, extra), "connected"
    assert fleet.owner_of(GID) is fleet.main                       # both there: the main bot by default
    assert fleet.owner_of("222222222222222222") is extra           # only the extra bot is there
    store.set_guild_bot(GID, extra.account_id)                     # the user picks the extra bot for this server
    assert fleet.owner_of(GID) is extra and extra.owns(GID) and not fleet.main.owns(GID)
    assert store.guild_lang(GID) == "en"                           # choosing a bot keeps the language...
    store.set_guild_lang(GID, "ru")
    assert store.guild_bot(GID) == extra.account_id                # ...and the language keeps the bot
    store.set_module(GID, "levels", True)
    main_bot = ManagedBot(fleet.main, {"members": True, "message_content": True})
    extra_bot = ManagedBot(extra, {"members": True, "message_content": True})
    guild = SimpleNamespace(id=int(GID))
    assert main_bot.cfg(guild, "levels") is None and extra_bot.cfg(guild, "levels") is not None  # no double answers
    assert {a["id"] for a in fleet.accounts_status()} == {"main", extra.account_id}

    calls = []

    async def fake_request(method, path, token, json=None):
        calls.append((method, path, json))
        return {"nick": json.get("nick"), "avatar": "abc", "user": {"id": "42"}}
    original = discord_rest._request
    discord_rest._request = fake_request
    try:
        png = "data:image/png;base64," + base64.b64encode(b"\x89PNG....").decode()
        member = asyncio.run(discord_rest.update_member_profile("tok", GID, nick="Helper", avatar=png, bio="Hi there"))
        asyncio.run(discord_rest.update_member_profile("tok", GID, reset=("avatar", "banner")))
    finally:
        discord_rest._request = original
    assert calls[0][1] == f"/guilds/{GID}/members/@me" and calls[0][2] == {"nick": "Helper", "avatar": png, "bio": "Hi there"}
    assert calls[1][2] == {"avatar": None, "banner": None}
    assert discord_rest.member_asset_url(GID, "42", member, "avatar").endswith(f"/guilds/{GID}/users/42/avatars/abc.png?size=256")
    try:
        asyncio.run(discord_rest.update_member_profile("tok", GID, nick="x" * 40))
        raise AssertionError("long nick accepted")
    except ValueError:
        pass
    store.close()
    print("ok several bot accounts: who runs each server, no double answers; per-server bot profile")


def test_custom_commands_v2_embed_language_autostart():
    import os

    from botd import autostart, catalog
    from botd.modules.custom_commands import CustomCommands, command_args, match_command, render
    from botd.modules.embeds import build_embed

    # responses with variables
    assert render("{args}", "Hello world") == "Hello world"
    for _ in range(50):
        n = int(render("{random}", "1 to 1000"))
        assert 1 <= n <= 1000
        assert 3 <= int(render("{random:3-5}")) <= 5
        assert render("{choose:a|b}") in ("a", "b")
    assert render("{arg2} {arg1} {arg9}", "x y") == "y x "
    assert render("{random}", "50 10") in {str(i) for i in range(10, 51)}  # order does not matter
    assert render("{__class__}", "x") == "{__class__}"                        # no access to Python objects
    cmds = catalog.validate_config("custom_commands", {"commands": [
        {"name": "type", "response": "{args}", "kind": "slash", "delete_trigger": True, "send_mode": "channel",
         "description": "Bot writes your message", "arg_hint": "text"},
        {"name": "rules", "response": "Be nice"}]})["commands"]
    assert cmds[0]["kind"] == "slash" and cmds[0]["delete_trigger"] is True and cmds[1]["kind"] == "prefix"
    assert match_command("!type hi", "!", cmds) is None            # slash-only commands do not react to text
    assert match_command("!rules", "!", cmds)["name"] == "rules" and command_args("!type Hello  world", "!") == "Hello  world"
    try:
        catalog.validate_config("custom_commands", {"commands": [{"name": "x", "response": "y", "kind": "nope"}]})
        raise AssertionError("bad kind accepted")
    except ValueError:
        pass

    async def text_command_deletes_trigger():
        store, bot = make_env()
        store.set_module(GID, "custom_commands", True)
        store.set_config(GID, "custom_commands", catalog.validate_config("custom_commands", {"prefix": "/", "commands": [
            {"name": "type", "response": "{args}", "kind": "prefix", "delete_trigger": True}]}))
        cog = CustomCommands(bot)
        msg = MagicMock(spec=discord.Message)
        msg.guild = SimpleNamespace(id=int(GID), name="Test", member_count=3)
        msg.author = MagicMock(bot=False, mention="<@1>", display_name="Dana", joined_at=None)
        msg.content, msg.delete = "/type Hello world", AsyncMock()
        msg.channel = MagicMock(mention="<#5>")
        msg.channel.send = AsyncMock()
        await cog.on_message(msg)
        assert msg.delete.await_count == 1 and msg.channel.send.await_args.args[0] == "Hello world"
        store.close()
    asyncio.run(text_command_deletes_trigger())

    # embed builder: author, fields, timestamp
    e = build_embed({"title": "Rules", "url": "https://x.y", "description": "Be **nice**", "color": "#112233", "author_name": "Staff",
                     "author_icon": "https://x/a.png", "fields": [{"name": "1", "value": "No spam", "inline": True}], "timestamp": True})
    assert e.author.name == "Staff" and e.fields[0].inline and e.timestamp is not None and e.url == "https://x.y"

    # one language everywhere: saved default texts follow the new language, own texts stay
    store, _ = make_env()
    d = catalog.module_defaults("welcome")
    store.set_config(GID, "welcome", catalog.strip_defaults("welcome", {**d, "channel_id": "333333333333333333"}))
    assert "welcome_message" not in store.raw_config(GID, "welcome")
    store.set_config(GID, "welcome", {"welcome_message": "My text", "leave_message": d["leave_message"]})
    store.set_guild_lang(GID, "en")
    assert store.raw_config(GID, "welcome") == {"welcome_message": "My text"}
    assert catalog.merged_config("welcome", store.raw_config(GID, "welcome"), "en")["leave_message"].endswith("left the server after {duration}.")
    assert store.get_settings()["keep_running_on_close"] is True   # keeps running in the background by default
    store.close()

    # start with Windows: a small script in the (here: temporary) Startup folder
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tempfile.mkdtemp()
    try:
        assert not autostart.is_enabled()
        autostart.set_enabled(True)
        text = autostart.script_path().read_text(encoding="utf-8")
        assert autostart.is_enabled() and "-m botd" in text and "CurrentDirectory" in text
        autostart.set_enabled(False)
        assert not autostart.is_enabled()
    finally:
        if old is not None:
            os.environ["APPDATA"] = old
    print("ok custom commands v2 (variables, slash/text, delete my message), embed builder, one language, start with Windows")


def test_bot_character():
    from botd import persona
    from botd.modules import ai_providers
    from botd.modules.claude_chat import ai_complete, character_prompt

    p = persona.clean({"enabled": True, "name": "Deadpool", "universe": "Marvel", "traits": ["chaotic", "chaotic", " "],
                       "length": 99, "vulgarity": -4, "content": 4, "category": "nope", "avatar_url": "javascript:x"})
    assert p["traits"] == ["chaotic"] and p["length"] == 3 and p["vulgarity"] == 0 and p["category"] == "original" and p["avatar_url"] == ""
    text = persona.build_prompt(p)
    assert text.startswith("You are Deadpool from Marvel.") and "Use everything you already know about Deadpool" in text
    assert "never explicit" not in text and "family-friendly" not in text  # content capped to level 2 outside NSFW channels
    assert "Mature themes" in text and "Adult humor" in persona.build_prompt(p, nsfw_channel=True)
    only_name = persona.build_prompt(persona.clean({"enabled": True, "name": "Benjamin Netanyahu"}))
    assert "might be a real" in only_name and "parody bot" in only_name   # real people are a parody even without a category
    try:
        persona.validate({"enabled": True, "name": ""})
        raise AssertionError("character without a name accepted")
    except ValueError:
        pass
    g = persona.parse_generated('Sure!\n```json\n{"description": "Merc with a mouth", "category": "movies", "traits": ["chaotic"], "facts": ["Heals fast"]}\n```')
    assert g["description"] == "Merc with a mouth" and g["category"] == "movies" and g["facts"] == ["Heals fast"]

    store, _ = make_env()
    guild = SimpleNamespace(id=int(GID))
    assert character_prompt(store, guild, None) == ""
    store.set_config(GID, "_persona", persona.validate({"enabled": True, "name": "Deadpool"}))
    store.set_config(GID, "claude_chat", {"system_prompt": "Never talk about cats."})
    cp = character_prompt(store, guild, SimpleNamespace(nsfw=False))
    assert cp.startswith("You are Deadpool.") and cp.endswith("Never talk about cats.")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ai_providers.PROVIDERS["openai"]["base"] = f"http://127.0.0.1:{server.server_port}/v1"
    from botd import secrets_store
    secrets_store.get = lambda name: {"openai_key": "sk-openai-test"}.get(name)
    NEXT.append({"model": "gpt-6-luna", "choices": [{"finish_reason": "stop", "message": {"content": "Maximum effort!"}}],
                 "usage": {"prompt_tokens": 20, "completion_tokens": 3}})
    try:
        reply = asyncio.run(ai_complete(store, {"provider": "openai", "openai_model": "luna"}, cp,
                                        [{"role": "user", "content": "hi"}]))
    finally:
        server.shutdown()
    assert reply == "Maximum effort!" and REQUESTS[-1]["body"]["messages"][0]["content"].startswith("You are Deadpool.")
    store.close()
    print("ok bot character: name-only works, sliders, NSFW cap, real-person parody rule, auto-fill parsing, chat uses it")


def test_character_saves_even_if_discord_refuses_the_profile():
    """Saving the Character must never be lost because Discord refused the bot's new name or picture."""
    import tempfile
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi.testclient import TestClient

    from botd import discord_rest
    from botd.api import create_app

    tmp = tempfile.mkdtemp()  # the database stays open while the app object exists
    if True:
        store = Store(Path(tmp) / "t.sqlite3")
        store.upsert_guild(GID, "Test")
        manager = MagicMock()
        manager.owner_of.return_value = MagicMock(token_name="bot_token")
        manager.live_guild.return_value = None
        app = create_app(manager, store, "tok", 8123, lambda: None)
        client = TestClient(app, base_url="http://127.0.0.1:8123")
        refused = AsyncMock(side_effect=discord_rest.DiscordAPIError(429, "You are changing your avatar too fast"))
        with patch.object(discord_rest, "update_member_profile", refused), patch("botd.secrets_store.get", return_value="x"):
            r = client.put(f"/api/guilds/{GID}/persona", headers={"X-Session-Token": "tok"},
                           json={"persona": {"enabled": True, "name": "Yeriangel", "traits": [f"t{i}" for i in range(25)]},
                                 "apply_profile": True, "avatar": None})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["warning"] and "too fast" in body["warning"]
        saved = store.get_guild(GID)["config"]["_persona"]
        assert saved["name"] == "Yeriangel" and saved["enabled"] and len(saved["traits"]) == 25  # up to 30 now
    print("ok character save")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
