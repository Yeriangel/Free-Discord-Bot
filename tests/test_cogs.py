"""Behaviour tests for the module cogs, using mocked Discord objects (no network)."""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from botd.bot import BotManager, ManagedBot
from botd.modules.automod import AutoMod
from botd.modules.custom_commands import CustomCommands
from botd.modules.moderation import Moderation, hierarchy_error
from botd.modules.restricted import RestrictedChannels
from botd.modules.warn_system import Warnings
from botd.modules.welcome import Welcome
from botd.store import Store

GID = "111111111111111111"
CH = 222222222222222222


def make_env():
    d = tempfile.mkdtemp()
    store = Store(Path(d) / "t.db")
    store.upsert_guild(GID, "Test")
    bot = ManagedBot(BotManager(store), {"members": True, "message_content": True})
    return store, bot


def role(pos):
    r = MagicMock(spec=discord.Role)
    r.position = pos
    r.__lt__ = lambda a, b: a.position < b.position
    r.__ge__ = lambda a, b: a.position >= b.position
    r.id = 900 + pos
    r.managed = False
    r.is_default = lambda: False
    r.name = f"role{pos}"
    return r


def member(mid, top, manage=False, bot=False):
    m = MagicMock(spec=discord.Member)
    m.id, m.bot, m.top_role, m.roles = mid, bot, role(top), []
    m.mention, m.display_name = f"<@{mid}>", f"user{mid}"
    m.guild_permissions = discord.Permissions(manage_messages=manage, moderate_members=True, kick_members=True)
    m.send = AsyncMock()
    m.timeout = AsyncMock()
    return m


def guild(channel):
    g = MagicMock(spec=discord.Guild)
    g.id, g.name, g.owner_id, g.member_count = int(GID), "Test", 1, 42
    g.me = member(2, 50, manage=True)
    g.me.guild_permissions = discord.Permissions(manage_messages=True, moderate_members=True, kick_members=True,
                                                 ban_members=True, manage_roles=True)
    g.get_channel = lambda cid: channel if cid == CH else None
    return g


def message(content, author, g, channel):
    msg = MagicMock(spec=discord.Message)
    msg.content, msg.author, msg.guild, msg.channel = content, author, g, channel
    msg.raw_mentions, msg.raw_role_mentions, msg.mention_everyone = [], [], False
    msg.delete = AsyncMock()
    return msg


def channel():
    c = MagicMock()
    c.id, c.mention = CH, f"<#{CH}>"
    c.send = AsyncMock()
    return c


async def scenario():
    store, bot = make_env()
    ch = channel()
    g = guild(ch)

    # custom commands: off -> silent, on -> reply with filled placeholders
    cc = CustomCommands(bot)
    user = member(10, 1)
    await cc.on_message(message("!rules", user, g, ch))
    assert ch.send.await_count == 0
    store.set_module(GID, "custom_commands", True)
    store.set_config(GID, "custom_commands", {"prefix": "!", "commands": [{"name": "rules", "response": "שלום {user} ב-{server}"}]})
    await cc.on_message(message("!rules", user, g, ch))
    assert ch.send.await_args.args[0] == "שלום <@10> ב-Test"
    assert ch.send.await_args.kwargs["allowed_mentions"].everyone is False
    print("ok custom commands")

    # custom command that toggles a role
    vip = role(3)
    g.get_role = lambda rid: vip if rid == vip.id else None
    user.add_roles, user.remove_roles = AsyncMock(), AsyncMock()
    store.set_config(GID, "custom_commands", {"prefix": "!", "commands": [
        {"name": "vip", "response": "", "role_id": str(vip.id), "role_action": "toggle"}]})
    await cc.on_message(message("!vip", user, g, ch))
    assert user.add_roles.await_count == 1 and "קיבלת את התפקיד" in ch.send.await_args.args[0]
    print("ok custom command role")

    # automod ruleset: banned word with action "both" -> delete + warning; warning #1 triggers timeout
    wcog = Warnings(bot)
    bot.get_cog = lambda name: wcog if name == "Warnings" else None
    store.set_module(GID, "warnings", True)
    store.set_config(GID, "warnings", {"dm_user": True, "window_days": 30,
                                       "steps": [{"count": 1, "action": "timeout", "minutes": 5}]})
    am = AutoMod(bot)
    store.set_module(GID, "automod", True)
    store.set_config(GID, "automod", {"words": {"on": True, "action": "both", "list": ["bad"]}})
    ch.send.reset_mock()
    m1 = message("this is bad", user, g, ch)
    await am.on_message(m1)
    assert m1.delete.await_count == 1 and ch.send.await_count == 1 and user.timeout.await_count == 1
    assert store.count_warnings(GID, str(user.id)) == 1 and user.send.await_count == 1  # recorded + DM
    # action "delete" only: no new warning
    store.set_config(GID, "automod", {"words": {"on": True, "action": "delete", "list": ["bad"]}})
    m1b = message("bad again", user, g, ch)
    await am.on_message(m1b)
    assert m1b.delete.await_count == 1 and store.count_warnings(GID, str(user.id)) == 1
    # rule switched off -> nothing happens
    store.set_config(GID, "automod", {"words": {"on": False, "action": "both", "list": ["bad"]}})
    m1c = message("bad", user, g, ch)
    await am.on_message(m1c)
    assert m1c.delete.await_count == 0
    mod = member(11, 5, manage=True)
    m2 = message("this is bad", mod, g, ch)
    await am.on_message(m2)
    assert m2.delete.await_count == 0
    # spam: 5 quick messages -> the 5th is deleted
    spammer = member(12, 1)
    msgs = [message(f"hi {i}", spammer, g, ch) for i in range(5)]
    for m in msgs:
        await am.on_message(m)
    assert [m.delete.await_count for m in msgs] == [0, 0, 0, 0, 1]
    print("ok automod rulesets + warning escalation")

    # escalation never touches someone above the bot
    boss = member(14, 60)
    boss.kick = AsyncMock()
    store.set_config(GID, "warnings", {"steps": [{"count": 1, "action": "kick", "minutes": 5}], "dm_user": False})
    _, _, action = await wcog.add_warning(g, boss, "test")
    assert boss.kick.await_count == 0 and "דולגה" in action
    print("ok escalation hierarchy")

    # restricted channel: images only
    rc = RestrictedChannels(bot)
    store.set_module(GID, "restricted_channels", True)
    store.set_config(GID, "restricted_channels", {"channels": [{"channel_id": str(CH), "mode": "images"}]})
    txt = message("just text", member(15, 1), g, ch)
    txt.type, txt.attachments, txt.embeds = discord.MessageType.default, [], []
    await rc.on_message(txt)
    img = message("", member(16, 1), g, ch)
    img.type, img.embeds = discord.MessageType.default, []
    img.attachments = [MagicMock(content_type="image/png")]
    await rc.on_message(img)
    assert txt.delete.await_count == 1 and img.delete.await_count == 0
    print("ok restricted channels")

    # welcome: fills placeholders and sends to the configured channel
    wc = Welcome(bot)
    store.set_module(GID, "welcome", True)
    store.set_config(GID, "welcome", {"channel_id": str(CH), "welcome_message": "היי {user}, חבר מספר {member_count}"})
    newbie = member(13, 1)
    newbie.guild = g
    ch.send.reset_mock()
    await wc.on_member_join(newbie)
    assert ch.send.await_args.args[0] == "היי <@13>, חבר מספר 42"
    print("ok welcome")

    # moderation: hierarchy checks and module toggle
    inv = member(20, 10)
    assert hierarchy_error(g, inv, inv) is not None                      # self
    assert hierarchy_error(g, inv, member(1, 1)) is not None             # owner
    assert hierarchy_error(g, inv, member(21, 10)) is not None           # equal role
    assert hierarchy_error(g, member(20, 60), member(21, 55)) is not None  # above the bot
    assert hierarchy_error(g, inv, member(21, 3)) is None
    mc = Moderation(bot)
    inter = MagicMock()
    inter.guild, inter.user = g, inv
    store.set_module(GID, "mod_commands", False)  # on by default; a server can turn it off
    assert "כבויות" in await mc._guard(inter, "kick_members", member(21, 3))
    store.set_module(GID, "mod_commands", True)
    assert await mc._guard(inter, "kick_members", member(21, 3)) is None
    assert "אין לך הרשאה" in await mc._guard(inter, "ban_members")
    print("ok moderation guards")

    await bot.close()
    store.close()


if __name__ == "__main__":
    asyncio.run(scenario())
