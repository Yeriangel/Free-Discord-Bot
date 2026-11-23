"""Claude chat tests: trigger rules, message splitting, and the real SDK request against a local mock API.

No real Anthropic calls are made - ANTHROPIC_BASE_URL points at a tiny local HTTP server.
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from botd import secrets_store
from botd.modules import claude_chat
from botd.modules.claude_chat import ClaudeChat, should_respond, split_message

USER = "123456789012345678"
CHAN = "222222222222222222"


def test_should_respond():
    base = dict(is_dm=False, author_id=USER, channel_id=CHAN, parent_id=None, mentioned=True,
                reply_to_bot=False, allowed_user=USER, allowed_channel="", reply_without_mention=False)
    assert should_respond(**base)
    assert not should_respond(**{**base, "author_id": "999999999999999999"})   # someone else
    assert should_respond(**{**base, "author_id": "999999999999999999", "allowed_user": ""})  # empty list = everyone
    assert should_respond(**{**base, "author_id": "999999999999999999", "allowed_user": f"{USER},999999999999999999"})  # in the list
    assert not should_respond(**{**base, "author_id": "888888888888888888", "allowed_user": f"{USER},999999999999999999"})
    assert should_respond(**{**base, "is_dm": True, "mentioned": False})       # DM
    assert not should_respond(**{**base, "mentioned": False})                  # no mention
    assert should_respond(**{**base, "mentioned": False, "reply_to_bot": True})
    assert not should_respond(**{**base, "allowed_channel": "333333333333333333"})  # wrong channel
    assert should_respond(**{**base, "allowed_channel": "333333333333333333", "parent_id": "333333333333333333"})  # thread
    assert should_respond(**{**base, "mentioned": False, "allowed_channel": CHAN, "reply_without_mention": True})
    assert not should_respond(**{**base, "mentioned": False, "reply_without_mention": True})  # needs a channel
    print("ok should_respond")


def test_split():
    assert split_message("hi") == ["hi"]
    long = ("שורה ארוכה " * 30 + "\n") * 20
    parts = split_message(long)
    assert all(len(p) <= 1900 for p in parts) and len(parts) > 1
    assert "".join(parts).replace(" ", "").replace("\n", "") == long.replace(" ", "").replace("\n", "")
    print("ok split_message")


# ---- mock Anthropic API ------------------------------------------------------
REQUESTS: list[dict] = []
NEXT: list[tuple[int, dict]] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        status, payload = NEXT.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def msg(text, stop="end_turn"):
    return 200, {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                 "content": [{"type": "text", "text": text}] if text else [],
                 "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 5}}


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def fake_message(text):
    m = MagicMock(spec=discord.Message)
    m.content, m.guild = text, None
    m.author = MagicMock(id=int(USER), bot=False)
    m.channel = MagicMock(id=int(CHAN))
    m.channel.typing = lambda: FakeTyping()
    m.channel.send = AsyncMock()
    m.reply = AsyncMock()
    return m


async def scenario(store):
    bot = MagicMock()
    bot.store = store
    cog = ClaudeChat(bot)
    cfg = {"system_prompt": "SYS", "effort": "low", "history_messages": 10, "reply_without_mention": False,
           "cooldown_seconds": 0, "daily_limit": 3}

    NEXT.extend([msg("שלום! איך אפשר לעזור?"), msg("זוכר: קוראים לך דני")])
    m1 = fake_message("קוראים לי דני")
    await cog._answer(m1, "קוראים לי דני", cfg)
    r = REQUESTS[-1]
    assert r["path"] == "/v1/messages", r["path"]
    assert r["body"]["model"] == "claude-haiku-5-5"
    assert r["body"]["output_config"] == {"effort": "low"}
    assert r["body"]["system"].startswith("SYS") and "same language" in r["body"]["system"] and r["body"]["messages"] == [{"role": "user", "content": "קוראים לי דני"}]
    assert "thinking" not in r["body"] and "sk-ant-test-key-000000" in (r["headers"].get("x-api-key", "") + r["headers"].get("authorization", ""))
    assert m1.reply.await_args.args[0] == "שלום! איך אפשר לעזור?"
    assert m1.reply.await_args.kwargs["allowed_mentions"].everyone is False

    m2 = fake_message("איך קוראים לי?")
    await cog._answer(m2, "איך קוראים לי?", cfg)
    sent = REQUESTS[-1]["body"]["messages"]
    assert [x["role"] for x in sent] == ["user", "assistant", "user"], sent   # history kept
    print("ok request shape + history")

    NEXT.append(msg("", stop="refusal"))
    m3 = fake_message("x")
    await cog._answer(m3, "x", cfg)
    assert "לא יכול לענות" in m3.reply.await_args.args[0]
    print("ok refusal")

    m4 = fake_message("y")  # 3 answers already today -> daily cap
    await cog._answer(m4, "y", cfg)
    assert "מגבלה היומית" in m4.reply.await_args.args[0] and len(NEXT) == 0
    print("ok daily limit")

    cfg["daily_limit"] = 0
    NEXT.append((401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
    m5 = fake_message("z")
    await cog._answer(m5, "z", cfg)
    assert "לא תקין" in m5.reply.await_args.args[0]
    print("ok auth error")

    usage = claude_chat.usage_summary(store)["days"][0]
    assert usage["messages"] == 3 and usage["input_tokens"] == 36, usage
    print("ok usage tracking", usage)

    # Sonnet: beta endpoint with server-side refusal fallback, priced per model
    cfg["model"] = "sonnet"
    reply = msg("שלום מ-Sonnet")
    reply[1]["model"] = "claude-sonnet-5-5"
    NEXT.append(reply)
    m6 = fake_message("hi")
    await cog._answer(m6, "hi", cfg)
    r = REQUESTS[-1]
    assert r["body"]["model"] == "claude-sonnet-5-5" and r["body"]["fallbacks"] == "default", r["body"]
    assert "server-side-fallback-2026-07-01" in r["headers"].get("anthropic-beta", ""), r["headers"]
    assert m6.reply.await_args.args[0] == "שלום מ-Sonnet"
    day = claude_chat.usage_summary(store)["days"][0]
    expected = 36 / 1e6 * 0.10 + 15 / 1e6 * 0.50 + 12 / 1e6 * 2.0 + 5 / 1e6 * 10.0
    assert abs(day["est_cost_usd"] - round(expected, 4)) < 1e-9, day
    print("ok sonnet with fallbacks and per-model cost")

    # Claude proposes a server action -> plan + confirm button -> runs only after confirmation
    from botd.modules import claude_actions, moderation
    cfg.update(model="haiku", allow_actions=True)
    guild = MagicMock(spec=discord.Guild)
    guild.name, guild.owner_id, guild.channels, guild.roles = "Test", 1, [], []
    me = MagicMock(spec=discord.Member)
    me.id, me.guild_permissions, me.top_role = 2, discord.Permissions(moderate_members=True), MagicMock(position=50)
    guild.me = me
    target = MagicMock(spec=discord.Member)
    target.id, target.display_name, target.timeout = 77, "User12345", AsyncMock()
    target.top_role = MagicMock(position=1)
    target.top_role.__ge__ = lambda a, b: a.position >= b.position
    guild.get_member = lambda uid: target if uid == 77 else None
    author = MagicMock(spec=discord.Member)
    author.id, author.bot, author.guild_permissions = int(USER), False, discord.Permissions(moderate_members=True)
    author.top_role = MagicMock(position=10)
    m7 = fake_message("@bot timeout User12345")
    m7.guild, m7.author = guild, author
    prompt = MagicMock()
    prompt.edit = AsyncMock()
    m7.reply = AsyncMock(return_value=prompt)
    NEXT.append((200, {"id": "msg_2", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                       "content": [{"type": "tool_use", "id": "toolu_1", "name": "timeout_member",
                                    "input": {"user": "<@77>", "minutes": 10, "reason": "spam"}}],
                       "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 20}}))

    async def fake_wait(view):
        view.value = True
        view.interaction = MagicMock()
        view.interaction.response.edit_message = AsyncMock()
        return False
    moderation.ConfirmView.wait = fake_wait
    await cog._answer(m7, "timeout User12345", cfg)
    body = REQUESTS[-1]["body"]
    assert any(t["name"] == "timeout_member" and t["strict"] for t in body["tools"]) and "Server: Test" in body["system"]
    assert "השתקה של **User12345** ל-10 דקות" in m7.reply.await_args.args[0]          # plan shown first
    assert target.timeout.await_count == 1                                              # ran after confirm
    assert "✅" in prompt.edit.await_args.kwargs["content"]
    # without the user's own permission nothing runs
    author.guild_permissions = discord.Permissions.none()
    p = claude_actions.plan(guild, author, "timeout_member", {"user": "<@77>", "minutes": 5}, "en")
    assert p.error == "You don't have permission to do this."
    print("ok claude server actions: plan, confirm, permission check")

    # "who talked the most?" -> read tool answered by the bot, then Claude writes the answer
    guild.id = 111111111111111111
    store.count_message(str(guild.id), "77", "User12345")
    NEXT.append((200, {"id": "msg_3", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                       "content": [{"type": "tool_use", "id": "toolu_2", "name": "top_members", "input": {"limit": 5}}],
                       "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 40, "output_tokens": 10}}))
    NEXT.append(msg("User12345 talked the most (1 message)."))
    m8 = fake_message("who talked the most on this server?")
    m8.guild, m8.author = guild, author
    before = len(REQUESTS)
    await cog._answer(m8, "who talked the most on this server?", cfg)
    assert len(REQUESTS) == before + 2
    follow = REQUESTS[-1]["body"]["messages"]
    assert follow[-1]["content"][0]["type"] == "tool_result" and "User12345" in follow[-1]["content"][0]["content"]
    assert m8.reply.await_args.args[0] == "User12345 talked the most (1 message)."
    print("ok claude read tools: lookup answered in the same reply")
    await cog.cog_unload()


def main():
    test_should_respond()
    test_split()
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
    secrets_store.get = lambda name: "sk-ant-test-key-000000" if name == "anthropic_key" else None
    from botd.store import Store

    store = Store(Path(tempfile.mkdtemp()) / "t.db")
    try:
        asyncio.run(scenario(store))
    finally:
        store.close()
        server.shutdown()


if __name__ == "__main__":
    main()
