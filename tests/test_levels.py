"""Levels tests: level math, reward rules, no-XP rules, XP storage, and the cog with mocked Discord objects."""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from botd import catalog
from botd.bot import BotManager, ManagedBot
from botd.modules import levels as lv
from botd.store import Store

GID, CH = "111111111111111111", 222222222222222222


def test_math():
    assert [lv.xp_to_next(n) for n in range(4)] == [100, 155, 220, 295]
    assert lv.total_xp_for_level(2) == 255
    assert lv.level_info(0) == (0, 0, 100)
    assert lv.level_info(99) == (0, 99, 100)
    assert lv.level_info(100) == (1, 0, 155)
    assert lv.level_info(255) == (2, 0, 220)
    for level in range(0, 60, 7):
        assert lv.level_info(lv.total_xp_for_level(level))[0] == level
    assert lv.progress_bar(5, 10, 10) == "▰" * 5 + "▱" * 5
    print("ok level math")


def test_rewards():
    rewards = [{"level": 5, "role_id": "A"}, {"level": 10, "role_id": "B"}, {"level": 20, "role_id": "C"}]
    assert lv.reward_plan(rewards, 12, "stack", True, set()) == ({"A", "B"}, set())
    assert lv.reward_plan(rewards, 12, "highest", True, {"A"}) == ({"B"}, {"A"})
    assert lv.reward_plan(rewards, 3, "stack", True, {"A", "B"}) == (set(), {"A", "B"})      # lost XP
    assert lv.reward_plan(rewards, 3, "stack", False, {"A", "B"}) == (set(), set())          # keep on loss
    assert lv.reward_plan(rewards, 7, "highest", False, {"B"}) == ({"A"}, set())             # keep higher role
    assert lv.reward_plan(rewards, 25, "stack", True, {"A", "B", "C"}) == (set(), set())
    print("ok reward rules")


def test_no_xp():
    cfg = catalog.module_defaults("levels")
    assert lv.gets_xp({"r1"}, {"c1"}, cfg)
    cfg["no_xp_role_ids"], cfg["no_xp_channel_ids"] = ["muted"], ["spam"]
    assert not lv.gets_xp({"muted"}, {"c1"}, cfg) and not lv.gets_xp({"r1"}, {"spam"}, cfg)
    cfg["channels_mode"] = "allow"
    assert lv.gets_xp({"r1"}, {"spam"}, cfg) and not lv.gets_xp({"r1"}, {"general"}, cfg)
    cfg["roles_mode"] = "allow"
    assert lv.gets_xp({"muted"}, {"spam"}, cfg) and not lv.gets_xp({"r1"}, {"spam"}, cfg)
    print("ok no-xp rules")


def test_store():
    s = Store(Path(tempfile.mkdtemp()) / "t.db")
    assert s.gain_xp(GID, "1", "a", 20, now=1000, cooldown=60) == (0, 20)
    assert s.gain_xp(GID, "1", "a", 20, now=1030, cooldown=60) is None          # cooldown
    assert s.gain_xp(GID, "1", "a", 20, now=1061, cooldown=60) == (20, 40)
    s.gain_xp(GID, "2", "b", 100, now=1000, cooldown=60)
    s.gain_xp("999999999999999999", "3", "other server", 500, now=1000, cooldown=60)
    assert [r["user_id"] for r in s.leaderboard(GID)] == ["2", "1"]              # per server
    assert s.rank_of(GID, "1") == 2 and s.rank_of(GID, "2") == 1
    assert s.change_xp(GID, "1", "a", -1000) == (40, 0)                          # never below zero
    assert s.get_xp(GID, "1")["messages"] == 2
    assert s.reset_xp(GID, "2") and s.leaderboard(GID) == []
    s.close()
    print("ok xp storage")


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
    cog = lv.Levels(bot)

    reward = role(555555555555555555, 5)
    g = MagicMock(spec=discord.Guild)
    g.id, g.name = int(GID), "Test"
    g.me = MagicMock(spec=discord.Member)
    g.me.top_role = role(1, 50)
    g.me.guild_permissions = discord.Permissions(manage_roles=True)
    g.get_role = lambda rid: reward if rid == reward.id else None

    ch = MagicMock()
    ch.id, ch.send = CH, AsyncMock()
    user = MagicMock(spec=discord.Member)
    user.id, user.bot, user.guild, user.roles = 42, False, g, []
    user.mention, user.display_name = "<@42>", "Dani"
    user.add_roles, user.remove_roles = AsyncMock(), AsyncMock()

    def msg():
        m = MagicMock(spec=discord.Message)
        m.guild, m.author, m.channel = g, user, ch
        return m

    # module off -> no XP
    await cog.on_message(msg())
    assert store.get_xp(GID, "42")["xp"] == 0

    store.set_module(GID, "levels", True)
    store.set_config(GID, "levels", catalog.validate_config("levels", {
        "rate": "3", "rewards": [{"level": 1, "role_id": str(reward.id)}], "announce_message": "GG {player} → {level}"}))
    with patch.object(lv.random, "randint", return_value=20):
        await cog.on_message(msg())                     # 20 * x3 = 60 XP
        assert store.get_xp(GID, "42")["xp"] == 60 and ch.send.await_count == 0
        await cog.on_message(msg())                     # cooldown: still 60
        assert store.get_xp(GID, "42")["xp"] == 60
        store._db.execute("UPDATE xp SET last_xp=0")    # skip the cooldown
        await cog.on_message(msg())                     # 120 XP -> level 1
    assert store.get_xp(GID, "42")["xp"] == 120
    assert ch.send.await_args.args[0] == "GG <@42> → 1"
    assert user.add_roles.await_args.args == (reward,)
    print("ok xp gain, cooldown, rate, level-up message, role reward")

    # manual removal drops below level 1 -> reward role removed
    user.roles = [reward]
    old, new = store.change_xp(GID, "42", "Dani", -100)
    await cog.apply_change(user, old, new, bot.cfg(g, "levels"), ch)
    assert user.remove_roles.await_args.args == (reward,)
    print("ok remove on XP loss")

    # no-XP channel
    store.set_config(GID, "levels", catalog.validate_config("levels", {"no_xp_channel_ids": [str(CH)]}))
    store._db.execute("UPDATE xp SET last_xp=0")
    await cog.on_message(msg())
    assert store.get_xp(GID, "42")["xp"] == 20
    print("ok no-xp channel")

    # rank embed
    embed = cog._rank_embed(user, bot.cfg(g, "levels"))
    assert "רמה 0" in embed.title and any(f.name == "XP כולל" for f in embed.fields)
    print("ok rank card")
    await bot.close()
    store.close()


if __name__ == "__main__":
    test_math()
    test_rewards()
    test_no_xp()
    test_store()
    asyncio.run(scenario())
