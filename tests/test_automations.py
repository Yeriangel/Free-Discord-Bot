"""Automations v2 tests (WHEN -> IF -> DO): validation, migration, conditions, and the engine with mocked Discord."""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from botd import catalog
from botd.automations_schema import migrate_v1, validate_automations
from botd.bot import BotManager, ManagedBot
from botd.modules.automations import Automations, Event, automation_matches, condition_passes
from botd.store import Store

GID, CH, LOGCH, CAT = "111111111111111111", 222222222222222222, 333333333333333333, 444444444444444444
ROLE = 555555555555555555


def auto(**kw):
    base = {"name": "t", "trigger": "message_send", "conditions": [{"type": "content_contains", "values": ["hi"]}],
            "actions": [{"type": "reply", "text": "yo"}]}
    base.update(kw)
    return validate_automations([base])[0]


def test_validation():
    a = auto()
    assert len(a["id"]) == 8 and a["enabled"] and a["ignore_bots"] and not a["retry"]
    bad = [
        {"trigger": "member_join", "actions": [{"type": "reply", "text": "x"}]},                       # no message to reply to
        {"trigger": "member_join", "actions": [{"type": "send_message", "target": "same_channel", "text": "x"}]},
        {"trigger": "member_join", "conditions": [{"type": "content_contains", "values": ["x"]}],      # condition not for this event
         "actions": [{"type": "send_message", "target": "dm", "text": "x"}]},
        {"trigger": "message_send", "conditions": [{"type": "content_contains", "values": []}], "actions": [{"type": "reply", "text": "x"}]},
        {"trigger": "message_send", "actions": []},
        {"trigger": "message_send", "actions": [{"type": "add_roles", "roles": []}]},
        {"trigger": "explode", "actions": [{"type": "reply", "text": "x"}]},
        {"trigger": "", "actions": [{"type": "reply", "text": "x"}]},
        {"trigger": "message_send", "actions": [{"type": "give_coins", "amount": 0}]},
        {"trigger": "message_send", "actions": [{"type": "send_message", "target": "dm", "text": "x", "buttons": ["a"] * 6}]},
    ]
    for b in bad:
        try:
            validate_automations([{"name": "x", **b}])
            raise AssertionError(b)
        except ValueError:
            pass
    try:
        validate_automations([auto()] * 51)
        raise AssertionError("limit")
    except ValueError:
        pass
    two = validate_automations([a, a])
    assert two[0]["id"] != two[1]["id"]
    ok = validate_automations([{"name": "v", "trigger": "voice_join", "conditions": [{"type": "in_channels", "values": [str(CH)]}],
                                "actions": [{"type": "give_xp", "amount": "25"}, {"type": "send_message", "target": "channel",
                                                                                  "channel_id": str(LOGCH), "text": "hi", "buttons": "A\nB"}],
                                "retry": True}])[0]
    assert ok["actions"][0]["amount"] == 25 and ok["actions"][1]["buttons"] == ["A", "B"] and ok["retry"]
    print("ok validation")


def test_migration():
    old = {"name": "old", "cooldown": 30, "trigger": {"type": "message", "match": "exact", "text": "!v", "channel_id": str(CH)},
           "conditions": {"required_role_id": str(ROLE), "ignore_bots": False},
           "actions": [{"type": "add_role", "role_id": str(ROLE)}, {"type": "delete_message"}]}
    new = validate_automations([old])[0]
    assert new["trigger"] == "message_send" and not new["ignore_bots"] and new["cooldown"] == 30
    assert {c["type"] for c in new["conditions"]} == {"in_channels", "content_exact", "has_any_role"}
    assert new["actions"][0] == {"type": "add_roles", "roles": [str(ROLE)]}
    assert migrate_v1({"trigger": "message_send"}) == {"trigger": "message_send"}  # already new
    print("ok migration from the first version")


def test_conditions():
    member = MagicMock(id=7, bot=False)
    member.roles = [MagicMock(id=ROLE)]
    ch = MagicMock(id=CH, category_id=CAT)
    ev = Event("message_send", member=member, channel=ch, content="Hello  World", attachments=["image/png"], is_reply=True)
    c = lambda t, *v: condition_passes({"type": t, "values": list(v)}, ev)  # noqa: E731
    assert c("in_channels", str(CH)) and c("in_channels", str(CAT)) and not c("in_channels", "1")
    assert c("has_any_role", str(ROLE), "1") and not c("has_all_roles", str(ROLE), "1") and c("has_all_roles", str(ROLE))
    assert c("lacks_any_role", str(ROLE), "1") and not c("lacks_all_roles", str(ROLE)) and c("lacks_all_roles", "1")
    assert c("user_is", "7") and not c("user_is", "8")
    assert c("content_contains", "hello world") and c("content_exact", "HELLO WORLD") and c("content_starts", "hel")
    assert not c("content_not_contains", "world") and c("content_not_contains", "bye")
    assert c("has_image") and not c("has_video") and c("is_reply") and not c("is_not_reply")
    rev = Event("reaction_add", member=member, emoji="⭐")
    assert condition_passes({"type": "reacted_with", "values": ["⭐"]}, rev)
    bev = Event("button_click", member=member, button="Get Role")
    assert condition_passes({"type": "button_is", "values": ["get role"]}, bev)
    a = auto()
    assert automation_matches(a, Event("message_send", member=member, content="say hi"))
    member.bot = True
    assert not automation_matches(a, Event("message_send", member=member, content="say hi"))  # ignore bots
    print("ok conditions")


def role(rid, pos):
    r = MagicMock(spec=discord.Role)
    r.id, r.position, r.managed, r.name = rid, pos, False, f"role{rid}"
    r.is_default = lambda: False
    r.__lt__ = lambda a, b: a.position < b.position
    return r


async def scenario():
    store = Store(Path(tempfile.mkdtemp()) / "t.db")
    store.upsert_guild(GID, "Test")
    bot = ManagedBot(BotManager(store), {"members": True, "message_content": True})
    cog = Automations(bot)

    verified = role(ROLE, 5)
    ch, logch = MagicMock(), MagicMock()
    ch.id, ch.mention, ch.send, ch.category_id = CH, f"<#{CH}>", AsyncMock(), None
    logch.id, logch.send = LOGCH, AsyncMock()
    g = MagicMock(spec=discord.Guild)
    g.id, g.name, g.member_count = int(GID), "Test", 10
    g.me = MagicMock(spec=discord.Member)
    g.me.top_role, g.me.guild_permissions = role(1, 50), discord.Permissions(manage_roles=True)
    g.get_role = lambda rid: verified if rid == ROLE else None
    g.get_channel = lambda cid: {CH: ch, LOGCH: logch}.get(cid)

    user = MagicMock(spec=discord.Member)
    user.id, user.bot, user.guild, user.roles, user.mention, user.display_name = 42, False, g, [], "<@42>", "Dani"
    user.add_roles, user.send = AsyncMock(), AsyncMock()

    def message(text):
        m = MagicMock(spec=discord.Message)
        m.guild, m.author, m.channel, m.content, m.attachments, m.reference = g, user, ch, text, [], None
        m.reply, m.delete, m.add_reaction, m.pin = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        return m

    store.set_module(GID, "automations", True)
    store.set_config(GID, "automations", catalog.validate_config("automations", {"items": [
        {"name": "verify", "cooldown": 30, "trigger": "message_send",
         "conditions": [{"type": "content_exact", "values": ["!verify"]}, {"type": "in_channels", "values": [str(CH)]}],
         "actions": [{"type": "add_roles", "roles": [str(ROLE)]}, {"type": "give_coins", "amount": 50},
                     {"type": "give_xp", "amount": 120}, {"type": "delete_message"},
                     {"type": "send_message", "target": "dm", "text": "ok {server}"}, {"type": "add_reaction", "emoji": "✅"}]},
        {"name": "announce", "trigger": "role_gain", "conditions": [{"type": "gained_role", "values": [str(ROLE)]}],
         "actions": [{"type": "send_message", "target": "channel", "channel_id": str(LOGCH), "text": "{user} got {role}",
                      "buttons": ["Hi"]}]},
        {"name": "voice", "trigger": "voice_join", "conditions": [], "actions": [{"type": "take_coins", "amount": 10}]},
        {"name": "button", "trigger": "button_click", "conditions": [{"type": "button_is", "values": ["Hi"]}],
         "actions": [{"type": "remove_roles", "roles": [str(ROLE)]}]},
    ]}))

    m = message("!verify")
    await cog.on_message(m)
    assert user.add_roles.await_args.args == (verified,) and m.delete.await_count == 1
    assert store.wallet(GID, "42")["balance"] == 50 and store.get_xp(GID, "42")["xp"] == 120
    assert user.send.await_args.args[0] == "ok Test" and m.add_reaction.await_count == 0  # deleted -> no reaction
    m2 = message("!verify")
    await cog.on_message(m2)
    assert m2.delete.await_count == 0          # cooldown
    await cog.on_message(message("hello"))
    print("ok message trigger: roles, coins, XP, delete, DM, cooldown")

    before, after = MagicMock(spec=discord.Member), user
    before.roles, after.roles = [], [verified]
    await cog.on_member_update(before, after)
    assert logch.send.await_args.args[0] == f"<@42> got {verified.name}"
    assert logch.send.await_args.kwargs["view"].children[0].custom_id == "auto:Hi"
    print("ok role trigger + message with a button")

    vs_before, vs_after = MagicMock(channel=None), MagicMock(channel=ch)
    await cog.on_voice_state_update(user, vs_before, vs_after)
    assert store.wallet(GID, "42")["balance"] == 40
    print("ok voice trigger + take coins")

    user.remove_roles = AsyncMock()
    inter = MagicMock()
    inter.type, inter.data, inter.guild, inter.user, inter.channel = discord.InteractionType.component, {"custom_id": "auto:Hi"}, g, user, ch
    inter.response.defer = AsyncMock()
    await cog.on_interaction(inter)
    assert user.remove_roles.await_args.args == (verified,)
    print("ok button click trigger")

    # retry on failure: a 429 is retried
    import botd.modules.automations as am
    am.RETRY_DELAYS = (0, 0, 0)
    flaky = AsyncMock(side_effect=[discord.HTTPException(MagicMock(status=429, reason="rl"), "rate"), None])
    ch.send = flaky
    store.set_config(GID, "automations", catalog.validate_config("automations", {"items": [
        {"name": "r", "trigger": "message_send", "retry": True, "actions": [{"type": "send_message", "text": "x"}]}]}))
    await cog.on_message(message("anything"))
    assert flaky.await_count == 2
    print("ok retry on failure")

    store.set_module(GID, "automations", False)
    logch.send.reset_mock()
    await cog.on_member_join(user)
    assert logch.send.await_count == 0
    print("ok module off")
    await bot.close()
    store.close()


if __name__ == "__main__":
    test_validation()
    test_migration()
    test_conditions()
    asyncio.run(scenario())
