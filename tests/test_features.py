"""Tests for the newer modules, the intents fix and Claude's module tools (no network)."""
import asyncio
import datetime as dt
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from botd import catalog, discord_rest
from botd.bot import BotManager, ManagedBot
from botd.store import Store

GID = "111111111111111111"
USER = "123456789012345678"

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>My Blog</title>
<item><title>Second post</title><link>https://blog.example/2</link><guid>p2</guid></item>
<item><title>First post</title><link>https://blog.example/1</link><guid>p1</guid></item></channel></rss>"""
ATOM_YT = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <title>Cool Channel</title>
 <entry><id>yt:video:abc</id><yt:videoId>abc</yt:videoId><title>New video!</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=abc"/><author><name>Cool Channel</name></author></entry>
</feed>"""


def make_env():
    store = Store(Path(tempfile.mkdtemp()) / "t.db")
    store.upsert_guild(GID, "Test")
    bot = ManagedBot(BotManager(store), {"members": True, "message_content": True})
    return store, bot


def test_parsers():
    from botd.modules.birthdays import fmt_date, is_birthday_today, parse_date
    from botd.modules.common import parse_duration
    from botd.modules.polls import split_answers

    assert parse_duration("10m") == 600 and parse_duration("1h30m") == 5400 and parse_duration("90") == 5400
    assert parse_duration("2d") == 172800 and parse_duration("1w") == 604800
    assert parse_duration("soon") is None and parse_duration("10x") is None and parse_duration("") is None
    assert parse_date("24/12") == (12, 24, 0) and parse_date("24.12.1995") == (12, 24, 1995)
    assert parse_date("1995-12-24") == (12, 24, 1995) and parse_date("29/2") == (2, 29, 0)
    assert parse_date("31/2") is None and parse_date("12/24") is None and parse_date("hello") is None
    assert parse_date("1/1/2999") is None
    assert is_birthday_today(12, 24, dt.date(2026, 12, 24)) and not is_birthday_today(12, 24, dt.date(2026, 12, 25))
    assert is_birthday_today(2, 29, dt.date(2026, 2, 28)) and not is_birthday_today(2, 29, dt.date(2028, 2, 28))
    assert fmt_date({"month": 12, "day": 4, "year": 0}) == "04/12"
    assert split_answers("Pizza | Burger | Pizza | Sushi") == ["Pizza", "Burger", "Sushi"]
    assert split_answers("a, b") == ["a", "b"] and len(split_answers("|".join(str(i) for i in range(20)))) == 10
    print("ok parsers: durations, dates, 29 Feb, poll answers")


def test_feeds_and_invites():
    from botd.modules.invite_tracker import find_used_invite
    from botd.modules.social_alerts import fill_alert, parse_feed, reddit_sub, youtube_channel_id

    title, items = parse_feed(RSS)
    assert title == "My Blog" and [i["id"] for i in items] == ["p2", "p1"] and items[0]["url"] == "https://blog.example/2"
    title, items = parse_feed(ATOM_YT)
    assert title == "Cool Channel" and items == [{"id": "abc", "title": "New video!", "url": "https://www.youtube.com/watch?v=abc",
                                                  "author": "Cool Channel"}]
    cid = "UC" + "a" * 22
    assert youtube_channel_id(cid) == cid and youtube_channel_id(f"https://youtube.com/channel/{cid}/videos") == cid
    assert youtube_channel_id("@mrbeast") is None
    assert reddit_sub("https://www.reddit.com/r/python/") == "python" and reddit_sub("r/aww") == "aww" and reddit_sub("gaming") == "gaming"
    assert fill_alert("📣 {name}: {title}\n{url}", "Ch", "Hi", "https://x") == "📣 Ch: Hi\nhttps://x"
    inv = lambda code, uses: SimpleNamespace(code=code, uses=uses)  # noqa: E731
    assert find_used_invite({"a": 1, "b": 5}, [inv("a", 1), inv("b", 6)]).code == "b"
    assert find_used_invite({"a": 1}, [inv("a", 1), inv("new", 1)]).code == "new"
    assert find_used_invite({"a": 1, "b": 1}, [inv("a", 2), inv("b", 2)]) is None  # two changed: unclear
    print("ok feeds (RSS, YouTube Atom), source parsing, invite detection")


def test_store_and_embeds():
    from botd.modules.claude_chat import yes_no
    from botd.modules.embeds import build_embed
    from botd.modules.giveaways import draw, giveaway_embed
    from botd.modules.suggestions import suggestion_embed

    store, _bot = make_env()
    a = store.set_birthday(GID, "", "John", 12, 24)
    assert store.set_birthday(GID, "", "john", 12, 25) == a and len(store.list_birthdays(GID)) == 1
    store.set_birthday(GID, USER, "Dana", 1, 2, 1990)
    assert [r["user_name"] for r in store.birthdays_on(GID, 1, 2)] == ["Dana"]
    r1 = store.add_reminder(GID, "5", USER, "drink water", time.time() - 1)
    store.add_reminder(GID, "5", USER, "later", time.time() + 999)
    assert [r["id"] for r in store.due_reminders(time.time())] == [r1]
    assert not store.delete_reminder(r1, uid="999") and store.delete_reminder(r1, uid=USER)
    g = store.add_giveaway(GID, "5", "Nitro", 2, time.time() + 60, USER)
    assert store.toggle_entry(g, "1") and store.toggle_entry(g, "2") and not store.toggle_entry(g, "2")
    assert store.giveaway_entries(g) == ["1"] and store.due_giveaways(time.time()) == []
    winners = draw(["1", "2", "3"], 2)
    assert len(winners) == 2 and len(set(winners)) == 2 and draw(["1"], 3) == ["1"] and draw(["1"], 1, {"1"}) == []
    assert store.record_invite_join(GID, "m1", "inv", "Inviter") == 1 and store.record_invite_join(GID, "m2", "inv", "Inviter") == 2
    assert store.record_invite_leave(GID, "m1") == "inv" and store.invite_stats(GID, "inv") == {"joins": 2, "leaves": 1, "total": 1}
    store.open_ticket(GID, "t1", USER, "u", "")
    assert store.next_ticket_number(GID) == 2 and len(store.open_tickets_of(GID, USER)) == 1
    store.close_ticket("t1")
    assert store.open_tickets_of(GID, USER) == []
    sid, n = store.add_suggestion(GID, "5", USER, "u", "Add a music bot")
    store.set_suggestion_status(GID, n, "approved", "good idea")
    assert store.get_suggestion(GID, n)["status"] == "approved"
    store.set_afk(GID, USER, "lunch")
    assert store.get_afk(GID, USER)["reason"] == "lunch" and store.clear_afk(GID, USER) and store.get_afk(GID, USER) is None
    store.count_message(GID, USER, "Dana")
    store.count_message(GID, USER, "Dana")
    store.count_message(GID, "2", "Bob")
    assert [(r["user_name"], r["messages"]) for r in store.top_talkers(GID)] == [("Dana", 2), ("Bob", 1)]

    e = build_embed({"title": "Hi", "description": "", "color": "#FF0000", "image_url": "javascript:alert(1)",
                     "thumbnail_url": "https://x/y.png", "footer": "f"})
    assert e.color.value == 0xFF0000 and e.image.url is None and e.thumbnail.url == "https://x/y.png"
    try:
        build_embed({"title": "", "description": " "})
        raise AssertionError("empty embed")
    except ValueError:
        pass
    gw = store.get_giveaway(g)
    assert "Nitro" in giveaway_embed(gw, "en", 3).title and "Winners" in giveaway_embed(gw, "en", 3, ["1"]).description
    assert suggestion_embed(5, "x", None, "denied", "no", "en").fields[0].value == "❌ Denied"
    assert yes_no("Yes!") is True and yes_no("כן") is True and yes_no("нет") is False and yes_no("maybe later") is None
    store.close()
    print("ok feature tables, embeds, giveaway draw, yes/no")


def test_catalog_and_intents():
    assert not [m["key"] for m in catalog.MODULES if m["status"] == "soon"]
    assert catalog.MODULES_BY_KEY["automations"]["category"] == "automations"
    for m in catalog.MODULES:
        catalog.validate_config(m["key"], catalog.module_defaults(m["key"]))
    assert catalog.validate_config("temp_channels", {"lobby_channel_id": "333333333333333333"})["lobby_channel_id"] == "333333333333333333"

    assert discord_rest.intents_from_flags(1 << 15 | 1 << 19) == {"members": True, "message_content": True}
    assert discord_rest.intents_from_flags(1 << 14) == {"members": True, "message_content": False}
    calls = []

    async def fake_request(method, path, token, json=None):
        calls.append((method, path, json))
        if method == "GET":
            return {"id": "1", "flags": 1 << 23}
        if len(calls) == 2:  # first PATCH rejected because of a read-only flag
            raise discord_rest.DiscordAPIError(400, "bad flags")
        return {"flags": json["flags"]}

    original = discord_rest._request
    discord_rest._request = fake_request
    try:
        found = asyncio.run(discord_rest.enable_intents("tok"))
    finally:
        discord_rest._request = original
    assert found == {"members": True, "message_content": True}, found
    assert calls[1] == ("PATCH", "/applications/@me", {"flags": (1 << 23) | (1 << 15) | (1 << 19)})
    assert calls[2][2] == {"flags": (1 << 15) | (1 << 19)}  # retry with only the flags the API may change
    print("ok all modules built, automations category, intents switched on through the API")


def test_claude_module_tools():
    from botd.modules import claude_actions
    from botd.modules.claude_chat import ClaudeChat

    store, bot = make_env()
    store.set_module(GID, "birthdays", True)
    store.set_module(GID, "reminders", True)
    store.update_settings({"allowed_user_id": USER})
    guild = MagicMock(spec=discord.Guild)
    guild.id, guild.name, guild.channels, guild.roles, guild.member_count = int(GID), "Test", [], [], 10
    john = MagicMock(spec=discord.Member)
    john.id, john.name, john.display_name = 555, "john", "John"
    john.__str__ = lambda s: "john"
    guild.members = [john]
    guild.get_member = lambda uid: john if uid == 555 else None
    author = MagicMock(spec=discord.Member)
    author.id, author.guild, author.display_name = int(USER), guild, "Dana"
    author.guild_permissions = discord.Permissions.none()
    channel = MagicMock()
    channel.id = 777

    names = [t["name"] for t in claude_actions.available_tools(bot, guild, allow_actions=False)]
    assert {"add_birthday", "create_reminder", "top_members", "list_birthdays"} <= set(names)
    assert "start_giveaway" not in names and "timeout_member" not in names           # module off / actions off
    assert all("module" not in t and t["strict"] for t in claude_actions.available_tools(bot, guild, True))

    p = claude_actions.plan(guild, author, "add_birthday", {"person": "john", "date": "24/12"}, "en", bot, channel)
    assert not p.error and p.line == "🎂 Add to Birthdays: **John**, 24/12", p.line
    result = asyncio.run(claude_actions.execute(p, author, "en"))
    assert result.startswith("✅") and store.list_birthdays(GID)[0]["user_id"] == "555"
    p = claude_actions.plan(guild, author, "add_birthday", {"person": "Grandma", "date": "3/4/1950"}, "en", bot, channel)
    asyncio.run(claude_actions.execute(p, author, "en"))
    assert {r["user_name"] for r in store.list_birthdays(GID)} == {"John", "Grandma"}  # non-members by name
    p = claude_actions.plan(guild, author, "add_birthday", {"person": "john", "date": "40/40"}, "en", bot, channel)
    assert p.error.startswith("Invalid date")
    p = claude_actions.plan(guild, author, "create_reminder", {"text": "call mom", "in_minutes": 30, "for_user": ""}, "en", bot, channel)
    asyncio.run(claude_actions.execute(p, author, "en"))
    rem = store.list_reminders(GID)[0]
    assert rem["user_id"] == USER and rem["channel_id"] == "777" and 1700 < rem["due"] - time.time() <= 1800
    p = claude_actions.plan(guild, author, "start_giveaway", {"prize": "x", "duration_minutes": 5, "winners": 1, "channel_id": ""},
                            "en", bot, channel)
    assert "off" in p.error  # giveaways module is off
    store.count_message(GID, "555", "John")
    text = claude_actions.run_read_tool(bot, guild, "top_members", {"limit": 5})
    assert "John (<@555>): 1 messages" in text and "only while the bot was online" in text
    assert "Grandma: 03/04/1950" in claude_actions.run_read_tool(bot, guild, "list_birthdays", {})
    ctx = claude_actions.server_context(guild, names)
    assert "Today is" in ctx and "add_birthday" in ctx and "confirm button" in ctx

    async def typed_yes():
        cog = ClaudeChat(bot)
        bot.cfg = lambda g, key: {"reply_without_mention": False, "_lang": "en"} if key == "claude_chat" else None
        view = SimpleNamespace(value=None, stopped=False)
        view.stop = lambda: setattr(view, "stopped", True)
        cog.pending[777] = view
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock(id=int(USER), bot=False)
        msg.channel, msg.guild, msg.content, msg.reference, msg.mentions = channel, guild, "yes", None, []
        bot._connection.user = MagicMock(id=1)
        await cog.on_message(msg)
        assert view.value is True and view.stopped
        await cog.cog_unload()
    asyncio.run(typed_yes())
    store.close()
    print("ok claude module tools: birthday detection plan, saving, reminders, read tools, typed yes")


def test_module_listeners():
    """Runs the event handlers against mocked Discord objects."""
    from botd.modules.afk import AFK
    from botd.modules.birthdays import Birthdays
    from botd.modules.channels_auto import TempChannels, count_for
    from botd.modules.giveaways import Giveaways
    from botd.modules.reminders import Reminders
    from botd.modules.suggestions import Suggestions

    async def scenario():
        store, bot = make_env()
        for key in ("birthdays", "afk", "giveaways", "temp_channels", "suggestions", "reminders"):
            store.set_module(GID, key, True)
        chan = MagicMock(spec=discord.TextChannel)
        chan.id, chan.name, chan.mention = 333333333333333333, "general", "<#333>"
        sent = MagicMock()
        sent.id, sent.jump_url = 999, "https://discord.com/x"
        sent.add_reaction, sent.create_thread, sent.edit = AsyncMock(), AsyncMock(), AsyncMock()
        chan.send = AsyncMock(return_value=sent)
        chan.fetch_message = AsyncMock(return_value=sent)
        guild = MagicMock(spec=discord.Guild)
        guild.id, guild.name, guild.member_count, guild.premium_subscription_count = int(GID), "Test", 3, 2
        guild.roles, guild.channels = [1, 2, 3], [chan]
        guild.get_channel = lambda cid: chan if cid == chan.id else None
        dana = MagicMock(spec=discord.Member)
        dana.id, dana.bot, dana.display_name, dana.mention, dana.guild = int(USER), False, "Dana", f"<@{USER}>", guild
        dana.display_avatar = MagicMock(url="https://cdn/a.png")
        dana.send = AsyncMock()
        guild.get_member = lambda uid: dana if uid == int(USER) else None
        guild.get_role = lambda rid: None
        guild.members = [dana]
        bot.get_guild = lambda gid: guild if gid == int(GID) else None
        bot.get_channel = lambda cid: chan if cid == chan.id else None
        assert count_for(guild, "boosts", True) == "2" and count_for(guild, "roles", True) == "2" and count_for(guild, "bots", False) == "?"

        # birthdays: greeting posted once on the day
        store.set_config(GID, "birthdays", catalog.validate_config("birthdays", {"channel_id": str(chan.id), "hour": 0}))
        today = dt.date.today()
        store.set_birthday(GID, USER, "Dana", today.month, today.day, today.year - 30)
        store.set_birthday(GID, "", "Grandma", today.month, today.day)
        cog = Birthdays(bot)
        cog.loop.cancel()
        await cog._announce(guild, bot.cfg(guild, "birthdays"), today)
        texts = [c.args[0] for c in chan.send.await_args_list]
        assert any(f"<@{USER}>" in t for t in texts) and any("**Grandma**" in t for t in texts), texts
        chan.send.reset_mock()
        await cog._announce(guild, bot.cfg(guild, "birthdays"), today)
        assert chan.send.await_count == 0  # not twice in one year

        # personal reminder that is due
        store.add_reminder(GID, str(chan.id), USER, "drink water", time.time() - 5)
        rem = Reminders(bot)
        rem.loop.cancel()
        await rem._personal(time.time())
        assert "drink water" in chan.send.await_args.args[0] and store.due_reminders(time.time()) == []

        # AFK: mentioning an away member gets an automatic reply; writing again clears it
        store.set_afk(GID, USER, "lunch")
        afk = AFK(bot)
        other = MagicMock(spec=discord.Member)
        other.id, other.bot = 42, False
        msg = MagicMock(spec=discord.Message)
        msg.guild, msg.author, msg.mentions, msg.reply = guild, other, [dana], AsyncMock()
        await afk.on_message(msg)
        assert "lunch" in msg.reply.await_args.args[0]
        msg2 = MagicMock(spec=discord.Message)
        msg2.guild, msg2.author, msg2.mentions, msg2.reply = guild, dana, [], AsyncMock()
        await afk.on_message(msg2)
        assert store.get_afk(GID, USER) is None and msg2.reply.await_count == 1  # welcome back

        # giveaway ends with a winner from the entries
        gwc = Giveaways(bot)
        gwc.loop.cancel()
        gid = store.add_giveaway(GID, str(chan.id), "Nitro", 1, time.time() - 1, USER)
        store.set_giveaway_message(gid, "999")
        store.toggle_entry(gid, USER)
        winners = await gwc.finish(store.get_giveaway(gid))
        assert winners == [USER] and store.get_giveaway(gid)["ended"] == 1
        assert "Nitro" in chan.send.await_args.args[0]

        # suggestion card with votes and a thread
        store.set_config(GID, "suggestions", catalog.validate_config("suggestions", {"channel_id": str(chan.id)}))
        await Suggestions(bot).post(guild, dana, "Add a music channel")
        assert sent.add_reaction.await_count == 2 and sent.create_thread.await_count == 1
        assert store.list_suggestions(GID)[0]["message_id"] == "999"

        # temporary voice channel: created on joining the lobby, deleted when empty
        lobby = MagicMock(spec=discord.VoiceChannel)
        lobby.id, lobby.category, lobby.overwrites, lobby.members = 444444444444444444, None, {}, [dana]
        store.set_config(GID, "temp_channels", catalog.validate_config("temp_channels", {"lobby_channel_id": str(lobby.id)}))
        new_vc = MagicMock(spec=discord.VoiceChannel)
        new_vc.id, new_vc.members, new_vc.delete = 555555555555555555, [], AsyncMock()
        guild.create_voice_channel = AsyncMock(return_value=new_vc)
        dana.move_to = AsyncMock()
        tc = TempChannels(bot)
        await tc.on_voice_state_update(dana, SimpleNamespace(channel=None), SimpleNamespace(channel=lobby))
        assert guild.create_voice_channel.await_args.kwargs["name"] == "🔊 הערוץ של Dana" and dana.move_to.await_count == 1
        await tc.on_voice_state_update(dana, SimpleNamespace(channel=new_vc), SimpleNamespace(channel=None))
        assert new_vc.delete.await_count == 1 and store.get_kv(f"temp:{GID}") == []
        store.close()

    asyncio.run(scenario())
    print("ok listeners: birthdays, reminders, AFK, giveaway draw, suggestions, temporary channels")


def test_ai_member_tools():
    """The AI can list who is in the channel and draw random members fairly (not only the people who talked)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from botd.modules import claude_actions

    def member(i, name, bot=False, sees=True):
        return SimpleNamespace(id=i, name=name, display_name=name, bot=bot, sees=sees)

    people = [member(1, "Yeri"), member(2, "Dan"), member(3, "Helper", bot=True), member(4, "Hidden", sees=False),
              member(5, "C-Bot", bot=True)]
    guild = MagicMock()
    guild.members = people
    guild._state._intents.members = True
    channel = SimpleNamespace(name="general", permissions_for=lambda m: SimpleNamespace(view_channel=m.sees))
    names = {t["name"] for t in claude_actions.available_tools(MagicMock(cfg=lambda g, k: None), guild, False)}
    assert {"list_members", "pick_random_member"} <= names
    text = claude_actions.run_read_tool(None, guild, "list_members", {"scope": "channel", "include_bots": True}, channel)
    assert text.startswith("4 members in #general") and "Dan <@2>" in text and "Helper <@3> (bot)" in text
    assert "Hidden" not in text
    text = claude_actions.run_read_tool(None, guild, "list_members", {"scope": "server", "include_bots": False}, channel)
    assert text.startswith("3 members in the server") and "Helper" not in text
    seen = set()
    for _ in range(60):
        text = claude_actions.run_read_tool(None, guild, "pick_random_member",
                                            {"scope": "channel", "count": 1, "include_bots": False, "exclude_asker": True},
                                            channel, people[0])
        assert "from 1 members" in text and "<@2>" in text
        text = claude_actions.run_read_tool(None, guild, "pick_random_member",
                                            {"scope": "channel", "count": 1, "include_bots": True, "exclude_asker": False},
                                            channel, people[0])
        seen |= {i for i in (1, 2, 3, 5) if f"<@{i}>" in text}
    assert seen == {1, 2, 3, 5}  # everyone in the channel can be drawn
    text = claude_actions.run_read_tool(None, guild, "pick_random_member",
                                        {"scope": "channel", "count": 9, "include_bots": True, "exclude_asker": False},
                                        channel, people[0])
    assert "from 4 members" in text and text.count("<@") == 4
    guild._state._intents.members = False
    assert "intent is off" in claude_actions.run_read_tool(None, guild, "list_members",
                                                           {"scope": "channel", "include_bots": True}, channel)


def test_reaction_roles_embed_buttons_and_edit():
    """Reaction roles: embed + buttons, server emojis by name, editing a posted message, and button clicks."""
    import asyncio
    import tempfile
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from botd.modules import reaction_roles as rr

    store = Store(Path(tempfile.mkdtemp()) / "rr.sqlite3")
    store.upsert_guild(GID, "G")
    bot = MagicMock()
    bot.store = store
    bot.lang = lambda g: "en"
    bot.cfg = lambda g, k: {"_lang": "en"}
    cog = rr.ReactionRoles(bot)
    role_a = MagicMock(id=111, mention="<@&111>")
    role_a.name = "Jewish"
    role_b = MagicMock(id=222, mention="<@&222>")
    role_b.name = "Other"
    custom = MagicMock(available=True, animated=False)
    custom.name = "star_of_david"
    custom.__str__ = lambda self: "<:star_of_david:999>"
    message = MagicMock(id=555)
    message.edit = AsyncMock()
    message.add_reaction = AsyncMock()
    message.clear_reaction = AsyncMock()
    channel = MagicMock(id=777)
    channel.name = "roles"
    channel.permissions_for.return_value = SimpleNamespace(send_messages=True, read_message_history=True, add_reactions=True)
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(return_value=message)
    guild = MagicMock(id=int(GID), emojis=[custom])
    guild.get_role = lambda i: {111: role_a, 222: role_b}.get(i)

    with patch.object(rr, "text_channel", lambda g, cid: channel), patch.object(rr, "can_manage_role", lambda g, r: True):
        group = asyncio.run(cog.publish(guild, "777", content="", title="Religion?", style="embed", mode="buttons",
                                        color="#5865F2", pairs=[{"emoji": ":star_of_david:", "role_id": "111"},
                                                                {"emoji": "", "label": "Other", "role_id": "222"}]))
        sent = channel.send.call_args.kwargs
        assert sent["embed"].title == "Religion?" and sent["content"] is None
        buttons = sent["view"].children
        assert [b.custom_id for b in buttons] == ["rr:111", "rr:222"] and buttons[1].label == "Other"
        assert group["pairs"][0]["emoji"] == "<:star_of_david:999>"  # server emoji found by its name
        message.add_reaction.assert_not_called()  # buttons: no reactions
        # edit: switch to plain text with reactions, keep the same message
        asyncio.run(cog.update(guild, "555", "777", {"style": "text", "mode": "reactions", "title": "Pick one", "content": "",
                                                    "pairs": [{"emoji": "✅", "role_id": "111"}]}))
        edited = message.edit.call_args.kwargs
        assert edited["embed"] is None and edited["view"] is None and edited["content"].startswith("**Pick one**")
        message.add_reaction.assert_called_with("✅")
        saved = store.raw_config(GID, "reaction_roles")["groups"]
        assert len(saved) == 1 and saved[0]["mode"] == "reactions" and saved[0]["message_id"] == "555"
        # a half-filled row is refused
        try:
            asyncio.run(cog.update(guild, "555", "777", {"pairs": [{"emoji": "", "role_id": "111"}]}))
            raise AssertionError("should fail")
        except ValueError:
            pass

    # clicking a button toggles the role
    member = MagicMock(roles=[])
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    inter = MagicMock(type=rr.discord.InteractionType.component, data={"custom_id": "rr:111"}, guild=guild)
    inter.response.send_message = AsyncMock()
    inter.user = member
    with patch.object(rr, "can_manage_role", lambda g, r: True), patch.object(rr.discord, "Member", type(member)):
        asyncio.run(cog.on_interaction(inter))
        member.add_roles.assert_called_once()
        member.roles = [role_a]
        asyncio.run(cog.on_interaction(inter))
        member.remove_roles.assert_called_once()  # second click takes it away
    print("ok reaction roles: embed, buttons, server emoji, edit, toggle")


def test_restricted_kinds_tickets_and_social_rules():
    from botd.modules import restricted, social_alerts as sa, ticketing

    k = restricted.message_kinds
    assert k("hello", [], [], ["!"]) == {"text"} and k("see https://a.b/c", [], [], ["!"]) == {"links"}
    assert k("caption", ["image/png"], [], ["!"]) == {"images"} and k("", ["video/mp4"], [], ["!"]) == {"videos"}
    assert k("!rank", [], [], ["!"]) == {"commands"} and k("", [], [], ["!"], ["clip.MP4"]) == {"videos"}
    assert restricted.blocked_of({"mode": "media"}) == ["text", "links", "commands"]
    assert restricted.blocked_of({"blocked": ["links", "bogus"]}) == ["links"]
    # ticket buttons: one per type, own color; text that is not an emoji is not used as one
    view = ticketing.panel_view({"types": [{"emoji": "🛠️", "label": "Tech", "color": "green"}, {"emoji": "", "label": "Report", "color": "red"}]})
    assert [(b.custom_id, b.label, b.style.name) for b in view.children] == [("ticket:open:0", "Tech", "success"), ("ticket:open:1", "Report", "danger")]
    one = ticketing.panel_view({"button_label": "Open", "button_emoji": "hello", "button_color": "grey"}).children[0]
    assert one.custom_id == "ticket:open" and one.emoji is None and one.style.name == "secondary"
    assert ticketing.safe_emoji("<:x:123456789012345678>").id == 123456789012345678 and ticketing.safe_emoji("🎫") is not None
    # social alerts: what each alert announces
    assert sa.wants({"kind": "kick"}, "live") and not sa.wants({"kind": "kick"}, "new")
    assert sa.wants({"kind": "youtube", "event": "both"}, "live") and sa.wants({"kind": "youtube", "event": "both"}, "new")
    assert not sa.wants({"kind": "youtube"}, "live") and sa.wants({"kind": "telegram", "event": "live"}, "new")
    assert sa.simple_name("https://kick.com/XQC?x=1") == "xqc" and sa.bluesky_handle("@yeri") == "yeri.bsky.social"
    page = '<div data-post="chan/7"></div><div class="tgme_widget_message_text js-message_text" dir="auto">Hi <b>all</b><br/>new</div>'            '<div data-post="chan/8"></div><div class="tgme_widget_message_text" dir="auto">Second &amp; last</div>'
    items = sa.parse_telegram(page, "chan")
    assert items[0] == {"id": "chan/8", "title": "Second & last", "url": "https://t.me/chan/8", "author": "chan"} and items[1]["title"] == "Hi all new"
    print("ok restricted kinds, ticket buttons, social rules")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
