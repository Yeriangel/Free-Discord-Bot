"""Tests for the Home panel: the Discord login page on this computer, and sorting your servers."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ["BOTD_KEYRING_SERVICE"] = "FreeDiscordBot-HomeTest"  # never touch the real saved keys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from botd import discord_login, secrets_store  # noqa: E402

STORE: dict = {}


def fake_get(name):
    return STORE.get(name)


def fake_set(name, value):
    STORE[name] = value


def fake_delete(name):
    STORE.pop(name, None)


async def http(method: str, path: str, body: bytes = b"") -> tuple[int, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", discord_login.CALLBACK_PORT)
    writer.write(f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
    await writer.drain()
    data = await reader.read()
    writer.close()
    head, _, text = data.decode().partition("\r\n\r\n")
    return int(head.split(" ")[1]), text


def test_login_flow():
    async def run():
        flow = discord_login.LoginFlow()
        url = await flow.start("123456789012345678")
        assert "response_type=token" in url and "scope=identify+guilds" in url and "client_id=123456789012345678" in url
        assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A53134%2Fdiscord-callback" in url
        status, page = await http("GET", "/discord-callback")
        assert status == 200 and "access_token" in page
        status, _ = await http("POST", "/discord-token", json.dumps({"access_token": "abc", "state": "wrong"}).encode())
        assert status == 403 and discord_login.load_login() is None  # a page that does not know the state cannot log in
        body = json.dumps({"access_token": "tok-123", "expires_in": "604800", "state": flow.state}).encode()
        status, _ = await http("POST", "/discord-token", body)
        assert status == 200 and flow.done.is_set()
        assert discord_login.load_login() == "tok-123"
        await asyncio.sleep(0.2)
        assert flow.server is None  # the local page closes after the login
        discord_login.logout()
        assert discord_login.load_login() is None

    with patch.object(secrets_store, "get", fake_get), patch.object(secrets_store, "set", fake_set), \
            patch.object(secrets_store, "delete", fake_delete):
        asyncio.run(run())
    print("ok login flow")


def test_server_sorting():
    assert discord_login.can_manage({"owner": True}) and discord_login.can_manage({"permissions": str(1 << 5)})
    assert discord_login.can_manage({"permissions": str(1 << 3)}) and not discord_login.can_manage({"permissions": "1024"})

    class Resp:
        def __init__(self, data, status=200):
            self.status_code, self._d = status, data

        def json(self):
            return self._d

    async def fake_get(url, *a, **k):
        if url.endswith("/users/@me"):
            return Resp({"id": "42", "username": "yeri", "global_name": "Yeri", "avatar": None})
        return Resp([{"id": "1", "name": "Zeta", "permissions": "0"}, {"id": "2", "name": "Alpha", "owner": True},
                     {"id": "3", "name": "Mid", "permissions": str(1 << 5), "icon": "abc"}])

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        get = staticmethod(fake_get)

    with patch.object(discord_login.httpx, "AsyncClient", Client):
        data = asyncio.run(discord_login.home("t", {"3"}))
    assert data["user"]["name"] == "Yeri"
    assert [g["id"] for g in data["guilds"]] == ["3", "2", "1"]  # bot is there / you can add it / others
    assert data["guilds"][0]["bot_here"] and data["guilds"][0]["icon"].endswith("/icons/3/abc.png?size=128")
    print("ok server sorting")


def test_phone_pairing_rules():
    import tempfile

    from botd import mobile
    from botd.store import Store

    store = Store(Path(tempfile.mkdtemp()) / "m.sqlite3")
    phones = mobile.MobileAccess(store)
    assert mobile.is_tailscale("100.101.102.103") and not mobile.is_tailscale("192.168.1.5") and not mobile.is_tailscale("8.8.8.8")
    code = phones.new_code()
    try:
        phones.claim("999999" if code != "999999" else "111111", "x")
        raise AssertionError("wrong code must fail")
    except ValueError:
        pass
    token = phones.claim(code, "Pixel 9")
    dev = phones.device_for(token)
    assert dev and dev["name"] == "Pixel 9" and not dev["verified"] and "token_hash" in dev and token not in json.dumps(dev)
    try:
        phones.claim(code, "again")
        raise AssertionError("a code works once")
    except ValueError:
        pass
    try:
        phones.verify(dev["id"], {"id": "5", "username": "stranger"}, {"42"})
        raise AssertionError("another Discord account must be refused")
    except PermissionError:
        pass
    phones.verify(dev["id"], {"id": "42", "username": "yeri", "global_name": "Yeri"}, {"42"})
    dev = phones.device_for(token)
    assert dev["verified"] and dev["discord_name"] == "Yeri"
    assert phones.public_devices()[0].get("token_hash") is None  # the token's hash never leaves the PC
    assert mobile.pair_link("http://100.1.2.3:8765", "123456") == "freediscordbot://pair?url=http%3A%2F%2F100.1.2.3%3A8765&code=123456"
    assert phones.remove(dev["id"]) and phones.device_for(token) is None
    print("ok phone pairing rules")


if __name__ == "__main__":
    test_phone_pairing_rules()
    test_login_flow()
    test_server_sorting()
