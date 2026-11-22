"""Offline unit tests: redaction, store, catalog/config validation, AutoMod rules, helpers."""
import base64
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botd import catalog, discord_rest
from botd.redact import RedactingFormatter, redact, register_secret
from botd.store import Store

FAKE_DISCORD = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4OQ", "GabcDE", "abcdefghijklmnopqrstuvwxyz0123456789AB"])
FAKE_ANTHROPIC = "sk-ant-api03-" + "x" * 40


def test_redact_patterns():
    text = f"token={FAKE_DISCORD} key={FAKE_ANTHROPIC} Authorization: Bot abc.def.ghi"
    out = redact(text)
    assert FAKE_DISCORD not in out and FAKE_ANTHROPIC not in out and "abc.def.ghi" not in out
    register_secret("my-custom-secret-123")
    assert "my-custom-secret-123" not in redact("x my-custom-secret-123 y")


def test_redact_traceback():
    fmt = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError("boom " + FAKE_DISCORD)
    except RuntimeError:
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    assert FAKE_DISCORD not in fmt.format(rec)


def test_store_profiles_and_warnings():
    with tempfile.TemporaryDirectory() as d:
        s = Store(Path(d) / "t.db")
        s.update_settings({"allowed_user_id": "123456789012345678", "autostart_bot": 1})
        assert s.get_settings()["allowed_user_id"] == "123456789012345678"
        try:
            s.update_settings({"allowed_user_id": "abc"})
            raise AssertionError("should reject")
        except ValueError:
            pass
        a_id, b_id = "111111111111111111", "222222222222222222"
        s.upsert_guild(a_id, "A")
        s.upsert_guild(b_id, "B")
        s.set_config(a_id, "logs", {"channel_id": "333333333333333333"})
        s.set_module(b_id, "automod", True)
        a, b = s.get_guild(a_id), s.get_guild(b_id)
        assert catalog.merged_config("logs", a["config"].get("logs"))["channel_id"] == "333333333333333333"
        assert catalog.merged_config("logs", b["config"].get("logs"))["channel_id"] == ""  # separate profiles
        assert s.module_enabled(b_id, "automod") and not s.module_enabled(a_id, "automod")
        a["modules"]["x"] = True  # returned copies must not leak into the cache
        assert "x" not in s.get_guild(a_id)["modules"]
        # warnings are per server
        w1 = s.add_warning(a_id, "9", "u", "1", "mod", "r1")
        s.add_warning(a_id, "9", "u", "1", "mod", "r2")
        s.add_warning(b_id, "9", "u", "1", "mod", "other server")
        assert s.count_warnings(a_id, "9") == 2 and s.count_warnings(b_id, "9") == 1
        assert s.delete_warning(a_id, w1) and not s.delete_warning(b_id, w1)
        assert s.clear_warnings(a_id, "9") == 1 and s.count_warnings(a_id, "9") == 0
        s.add_tempban(a_id, "9", 100.0)
        assert [r["user_id"] for r in s.due_tempbans(150.0)] == ["9"] and s.due_tempbans(50.0) == []
        s.remove_tempban(a_id, "9")
        assert s.due_tempbans(1e12) == []
        s.close()


def test_validate_config():
    cfg = catalog.validate_config("automod", {
        "words": {"on": True, "action": "both", "list": "רע\nbad\n\nbad"},
        "spam": {"on": True, "action": "warn", "count": "7", "seconds": 5},
    })
    assert cfg["words"] == {"on": True, "action": "both", "list": ["רע", "bad"]}
    assert cfg["spam"]["count"] == 7 and cfg["invites"]["on"] is True  # untouched rules keep defaults
    for bad in ({"spam": {"on": True, "action": "warn", "count": 1}}, {"caps": {"on": True, "action": "nuke"}},
                {"exempt_role_ids": ["abc"]}):
        try:
            catalog.validate_config("automod", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    cmds = catalog.validate_config("custom_commands", {"commands": [
        {"name": "!Rules", "response": "x"}, {"name": "", "response": ""},
        {"name": "vip", "response": "", "role_id": "555555555555555555", "role_action": "toggle"}]})
    assert [c["name"] for c in cmds["commands"]] == ["rules", "vip"] and cmds["commands"][1]["role_action"] == "toggle"
    for bad in ([{"name": "a", "response": "1"}, {"name": "A", "response": "2"}],
                [{"name": "x", "response": "", "role_action": "add", "role_id": ""}]):
        try:
            catalog.validate_config("custom_commands", {"commands": bad})
            raise AssertionError(bad)
        except ValueError:
            pass
    steps = catalog.validate_config("warnings", {"steps": [{"count": "2", "action": "kick", "minutes": 5}]})["steps"]
    assert steps == [{"count": 2, "action": "kick", "minutes": 5}]
    try:
        catalog.validate_config("warnings", {"steps": [{"count": 1, "action": "explode", "minutes": 5}]})
        raise AssertionError("bad action")
    except ValueError:
        pass
    rc = catalog.validate_config("restricted_channels", {"channels": [{"channel_id": "", "mode": "images"},
                                                                      {"channel_id": "333333333333333333", "mode": "videos"}]})
    assert rc["channels"] == [{"channel_id": "333333333333333333", "mode": "videos"}]  # empty row dropped
    kept = catalog.validate_config("reaction_roles", {"groups": []}, {"groups": [{"message_id": "1"}]})
    assert kept["groups"] == [{"message_id": "1"}]


def test_automod_rules():
    from botd.modules.automod import RepeatTracker, SpamTracker, check_content, has_zalgo
    cfg = catalog.module_defaults("automod")
    for k in ("words", "links", "caps", "emoji", "spoilers", "zalgo"):
        cfg[k]["on"] = True
    cfg["words"]["list"] = ["מילה רעה", "bad"]
    cfg["links"]["allowed"] = ["youtube.com"]
    cfg["mentions"]["limit"] = 3
    assert check_content("join discord.gg/abc", cfg) == "invites"
    assert check_content("see https://evil.example/x", cfg) == "links"
    assert check_content("https://www.youtube.com/watch?v=1 and https://m.youtube.com", cfg) is None
    assert check_content("זו מילה רעה מאוד", cfg) == "words"
    assert check_content("BAD!", cfg) == "words"
    assert check_content("badge is fine", cfg) is None  # whole words only
    assert check_content("THIS IS ALL CAPS SHOUTING", cfg) == "caps"
    assert check_content("Normal sentence With A Few Caps", cfg) is None
    assert check_content("שלום עולם בעברית ארוכה מאוד מאוד", cfg) is None  # Hebrew has no caps
    assert check_content("😀" * 9, cfg) == "emoji" and check_content("😀" * 3, cfg) is None
    assert check_content("||a|| ||b|| ||c|| ||d|| ||e||", cfg) == "spoilers"
    assert check_content("hi", cfg, mention_count=4) == "mentions" and check_content("hi", cfg, mention_count=3) is None
    assert has_zalgo("h̀́̂̃i") and check_content("h̀́̂̃̄i", cfg) == "zalgo"
    assert not has_zalgo("בְּרֵאשִׁית שָׁלוֹם")  # Hebrew niqqud is not Zalgo
    cfg["invites"]["on"] = False
    cfg["links"]["on"] = False
    assert check_content("discord.gg/abc", cfg) is None  # rules switched off
    t = SpamTracker()
    assert [t.hit((1, 1), 3, 5, now=n) for n in (0, 1, 2)] == [False, False, True]
    assert [t.hit((1, 2), 3, 5, now=n) for n in (0, 10, 20)] == [False, False, False]
    r = RepeatTracker()
    assert [r.hit((1, 1), "Hello  World", 3, now=n) for n in (0, 1, 2)] == [False, False, True]
    assert [r.hit((1, 2), x, 3, now=0) for x in ("a", "b", "a")] == [False, False, False]


def test_helpers():
    from botd.modules.common import human_duration
    from botd.modules.custom_commands import match_command
    from botd.modules.reaction_roles import emoji_key
    from botd.modules.restricted import message_allowed
    from botd.modules.warn_system import pick_step
    cmds = [{"name": "rules", "response": "x"}]
    assert match_command("!rules please", "!", cmds) == cmds[0] and match_command("!RULES", "!", cmds) == cmds[0]
    assert match_command("rules", "!", cmds) is None and match_command("!", "!", cmds) is None
    assert emoji_key("<:pepe:123456789012345678>") == "123456789012345678" and emoji_key("😀") == "😀"
    assert message_allowed("images", "", ["image/png"], [], [])
    assert not message_allowed("images", "hello", [], [], [])
    assert message_allowed("images", "look", [], ["gifv"], [])
    assert message_allowed("videos", "", ["video/mp4"], [], []) and not message_allowed("videos", "", ["image/png"], [], [])
    assert message_allowed("media", "", ["video/mp4"], [], [])
    assert message_allowed("commands", "!rank", [], [], ["!"]) and not message_allowed("commands", "hi", [], [], ["!"])
    steps = [{"count": 3, "action": "timeout", "minutes": 60}, {"count": 5, "action": "ban", "minutes": 1}]
    assert pick_step(steps, 3)["action"] == "timeout" and pick_step(steps, 4) is None and pick_step(steps, 5)["action"] == "ban"
    assert human_duration(30) == "0 דקות" and human_duration(90) == "דקה" and human_duration(3 * 86400) == "3 ימים"


def test_permissions_minimal_by_default():
    perms = catalog.recommended_permissions(set())
    assert "administrator" not in perms and set(perms) == set(catalog.BASE_PERMS)
    perms = catalog.recommended_permissions({"mod_commands"})
    assert "ban_members" in perms and "administrator" not in perms
    url = catalog.invite_url("123", ["view_channel", "send_messages"], "456456456456456456")
    assert "permissions=3072" in url and "guild_id=456456456456456456" in url
    assert catalog.permissions_value(["administrator"]) == 8
    assert [c[0] for c in catalog.CATEGORIES] == ["essentials", "server_management", "utilities", "social", "games", "ai"]


def test_avatar_validation():
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG....").decode()
    assert discord_rest.validate_avatar_data_uri(png).startswith("data:image/png;base64,")
    for bad in ("data:text/html;base64,PGh0bWw+", "hello", "data:image/png;base64,***"):
        try:
            discord_rest.validate_avatar_data_uri(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
