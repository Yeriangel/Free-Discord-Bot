"""Economy tests: daily streaks, boosts, games, shop rules, storage, and the cog with mocked interactions."""
import asyncio
import datetime as dt
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from botd import catalog
from botd.bot import BotManager, ManagedBot
from botd.modules import economy as eco
from botd.store import Store

GID = "111111111111111111"
DAY = 86400


def test_rules():
    assert eco.daily_state(0, 0, 1000) == (True, 1, 0)
    assert eco.daily_state(1000, 1, 1000 + 3600)[0] is False                 # same day
    assert eco.daily_state(1000, 4, 1000 + DAY + 10)[:2] == (True, 5)          # streak continues
    assert eco.daily_state(1000, 4, 1000 + 3 * DAY)[:2] == (True, 1)           # missed -> reset
    assert eco.daily_amount(1, 100, 10, 30) == 100 and eco.daily_amount(5, 100, 10, 30) == 140
    assert eco.daily_amount(100, 100, 10, 30) == 400                           # capped at 30 bonus days
    today = dt.date(2026, 11, 22)
    boosts = [{"target": "daily", "percent": 50, "role_id": "", "until": ""},
              {"target": "daily", "percent": 25, "role_id": "vip", "until": ""},
              {"target": "daily", "percent": 100, "role_id": "", "until": "2026-11-01"},   # expired
              {"target": "losses", "percent": 40, "role_id": "", "until": "2026-12-31"}]
    assert eco.boost_percent(boosts, set(), "daily", today) == 50
    assert eco.boost_percent(boosts, {"vip"}, "daily", today) == 75
    assert eco.boost_percent(boosts, set(), "losses", today) == 40
    assert eco.apply_boost(100, 50, "daily") == 150 and eco.apply_boost(100, 40, "losses") == 60
    assert eco.apply_boost(100, 150, "losses") == 0                            # losses never go negative
    assert eco.roulette_profit("red", 1, 10) == 10 and eco.roulette_profit("red", 2, 10) == -10
    assert eco.roulette_profit("black", 2, 10) == 10 and eco.roulette_profit("black", 0, 10) == -10
    assert eco.roulette_profit("green", 0, 10) == 350 and eco.roulette_profit("17", 17, 10) == 350
    assert eco.rps_outcome("rock", "scissors") == 1 and eco.rps_outcome("rock", "paper") == -1
    assert eco.rps_outcome("paper", "paper") == 0
    print("ok economy rules")


def test_store():
    s = Store(Path(tempfile.mkdtemp()) / "t.db")
    assert s.wallet(GID, "1", "a", start=50)["balance"] == 50                  # starting balance once
    assert s.add_coins(GID, "1", "a", -60) is None and s.add_coins(GID, "1", "a", -50) == 0
    s.add_coins(GID, "1", "a", 100)
    assert s.transfer(GID, "1", "a", "2", "b", 70) and not s.transfer(GID, "1", "a", "2", "b", 31)
    assert s.wallet(GID, "2")["balance"] == 70
    assert s.buy_item(GID, "2", "b", "vip", 30, stock=1, per_user=0) == ("ok", 40)
    assert s.buy_item(GID, "1", "a", "vip", 30, stock=1, per_user=0)[0] == "sold_out"
    assert s.buy_item(GID, "2", "b", "cookie", 5, stock=0, per_user=1)[0] == "ok"
    assert s.buy_item(GID, "2", "b", "cookie", 5, stock=0, per_user=1)[0] == "limit"
    assert s.buy_item(GID, "1", "a", "cookie", 500, stock=0, per_user=0)[0] == "funds"
    assert {r["item"]: r["qty"] for r in s.inventory(GID, "2")} == {"vip": 1, "cookie": 1}
    assert [r["user_id"] for r in s.rich_list(GID)] == ["2", "1"]
    assert s.claim_daily(GID, "1", "a", 1000.0, 3, 120) == 150
    assert s.reset_wallet(GID, "2") and s.inventory(GID, "2") == []
    s.close()
    print("ok economy storage")


def test_config():
    cfg = catalog.validate_config("economy", {"items": [{"name": "VIP", "price": "500", "stock": 3}],
                                              "boosts": [{"target": "daily", "percent": 20, "until": "2027-01-01"}]})
    assert cfg["items"][0]["price"] == 500 and cfg["boosts"][0]["until"] == "2027-01-01"
    for bad in ({"items": [{"name": "a"}, {"name": "A"}]}, {"boosts": [{"target": "daily", "percent": 5, "until": "tomorrow"}]},
                {"min_bet": 100, "max_bet": 10}):
        try:
            catalog.validate_config("economy", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    print("ok economy config validation")


async def scenario():
    store = Store(Path(tempfile.mkdtemp()) / "t.db")
    store.upsert_guild(GID, "Test")
    bot = ManagedBot(BotManager(store), {"members": True, "message_content": True})
    cog = eco.Economy(bot)

    def inter():
        i = MagicMock()
        i.guild_id, i.guild = int(GID), MagicMock(id=int(GID))
        i.guild.name = "Test"
        i.user = MagicMock(spec=discord.Member)
        i.user.id, i.user.roles, i.user.mention = 42, [], "<@42>"
        i.user.__str__ = lambda self: "Dani"
        i.response.send_message = AsyncMock()
        return i

    i = inter()
    await cog.daily.callback(cog, i)
    assert "כבויה" in i.response.send_message.await_args.args[0]

    store.set_module(GID, "economy", True)
    store.set_config(GID, "economy", catalog.validate_config("economy", {
        "daily_base": 100, "boosts": [{"target": "daily", "percent": 50}],
        "items": [{"name": "Cookie", "price": 60, "reply": "🍪 טעים!"}]}))
    i = inter()
    await cog.daily.callback(cog, i)
    assert store.wallet(GID, "42")["balance"] == 150                            # 100 + 50% boost
    i = inter()
    await cog.daily.callback(cog, i)
    assert "כבר לקחת" in i.response.send_message.await_args.args[0]
    print("ok /daily with boost and cooldown")

    i = inter()
    with patch.object(eco.random, "randint", side_effect=[6, 6, 1, 1]):         # player 12 vs bot 2
        await cog.dice.callback(cog, i, 100)
    assert store.wallet(GID, "42")["balance"] == 250 and "זכית" in i.response.send_message.await_args.args[0]
    i = inter()
    await cog.dice.callback(cog, i, 999999)
    assert "בין" in i.response.send_message.await_args.args[0]                  # above max bet
    i = inter()
    with patch.object(eco.random, "randint", return_value=3):
        await cog.guess.callback(cog, i, 50, 7)                                 # wrong guess
    assert store.wallet(GID, "42")["balance"] == 200
    print("ok games and bet limits")

    i = inter()
    await cog.buy.callback(cog, i, "cookie")
    assert store.wallet(GID, "42")["balance"] == 140 and "טעים" in i.response.send_message.await_args.args[0]
    i = inter()
    await cog.buy.callback(cog, i, "nothing")
    assert "לא נמצא" in i.response.send_message.await_args.args[0]
    print("ok shop purchase")
    await bot.close()
    store.close()


if __name__ == "__main__":
    test_rules()
    test_store()
    test_config()
    asyncio.run(scenario())
