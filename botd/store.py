"""Non-secret settings and per-server profiles (module toggles + module settings), in SQLite under %APPDATA%."""

from .i18n import tr
import copy
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from .store_features import FeatureStoreMixin

DEFAULT_SETTINGS = {
    "allowed_user_id": "",
    "allowed_channel_id": "",
    "keep_running_on_close": True,
    "autostart_bot": False,
    "ui_lang": "he",
    "presence": {"status": "online", "activity": "none", "text": "", "url": ""},
    "appearance": {},
}

PRESENCE_STATUSES = ("online", "idle", "dnd", "invisible")
ACTIVITY_TYPES = ("none", "playing", "streaming", "listening", "watching", "competing", "custom")

_SNOWFLAKE = re.compile(r"^\d{15,21}$")


def valid_snowflake(value: str) -> bool:
    return value == "" or bool(_SNOWFLAKE.match(value))


class Store(FeatureStoreMixin):
    def __init__(self, path: Path):
        self._lock = threading.RLock()
        self._cache: dict[str, dict] = {}
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS guilds (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    icon TEXT NOT NULL DEFAULT '',
                    modules TEXT NOT NULL DEFAULT '{}',
                    config TEXT NOT NULL DEFAULT '{}',
                    updated REAL NOT NULL DEFAULT 0
                )"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL DEFAULT '',
                    moderator_id TEXT NOT NULL DEFAULT '', moderator_name TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '', created REAL NOT NULL
                )"""
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS warnings_by_user ON warnings(guild_id, user_id)")
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS tempbans (
                    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, until REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS xp (
                    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL DEFAULT '',
                    xp INTEGER NOT NULL DEFAULT 0, messages INTEGER NOT NULL DEFAULT 0, last_xp REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS xp_rank ON xp(guild_id, xp DESC)")
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS wallets (
                    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL DEFAULT '',
                    balance INTEGER NOT NULL DEFAULT 0, last_daily REAL NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS inventory (
                    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, item)
                )"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS item_sales (
                    guild_id TEXT NOT NULL, item TEXT NOT NULL, sold INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, item)
                )"""
            )
            cols = {r["name"] for r in self._db.execute("PRAGMA table_info(guilds)")}
            if "config" not in cols:  # upgrade from v0.1
                self._db.execute("ALTER TABLE guilds ADD COLUMN config TEXT NOT NULL DEFAULT '{}'")
        self._init_features()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- global settings -------------------------------------------------
    def get_settings(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM settings").fetchall()
        result = dict(DEFAULT_SETTINGS)
        for row in rows:
            if row["key"] in result:
                result[row["key"]] = json.loads(row["value"])
        return result

    def update_settings(self, changes: dict) -> dict:
        clean = {k: v for k, v in changes.items() if k in DEFAULT_SETTINGS}
        for key in ("allowed_user_id", "allowed_channel_id"):
            if key in clean:
                clean[key] = str(clean[key] or "").strip()
                if not valid_snowflake(clean[key]):
                    raise ValueError(tr("{key}: מזהה דיסקורד הוא מספר של 15–21 ספרות", key=key))
        if "ui_lang" in clean:
            from .i18n import set_ui_lang, valid_lang
            if not valid_lang(clean["ui_lang"]):
                raise ValueError("ui_lang")
            set_ui_lang(clean["ui_lang"])
        if "presence" in clean:
            pr = clean["presence"] if isinstance(clean["presence"], dict) else {}
            status, activity = pr.get("status", "online"), pr.get("activity", "none")
            text, url = str(pr.get("text") or "").strip(), str(pr.get("url") or "").strip()
            if status not in PRESENCE_STATUSES or activity not in ACTIVITY_TYPES:
                raise ValueError(tr("סטטוס או פעילות לא חוקיים"))
            if len(text) > 128:
                raise ValueError(tr("טקסט הסטטוס ארוך מדי (עד 128 תווים)"))
            if activity == "streaming" and not re.match(r"^https://(www\.)?(twitch\.tv|youtube\.com|youtu\.be)/\S+$", url):
                raise ValueError(tr("בסטרימינג צריך קישור ל-Twitch או YouTube"))
            if activity not in ("none",) and not text:
                raise ValueError(tr("צריך לכתוב טקסט לפעילות"))
            clean["presence"] = {"status": status, "activity": activity, "text": text, "url": url}
        if "appearance" in clean:
            from .customize import validate_appearance
            clean["appearance"] = validate_appearance(clean["appearance"] if isinstance(clean["appearance"], dict) else {})
        for key in ("keep_running_on_close", "autostart_bot"):
            if key in clean:
                clean[key] = bool(clean[key])
        with self._lock, self._db:
            for key, value in clean.items():
                self._db.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
        return self.get_settings()

    # ---- internal key/value (not user settings) -------------------------
    def get_kv(self, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_kv(self, key: str, value) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    # ---- guild profiles --------------------------------------------------
    def upsert_guild(self, gid: str, name: str, icon: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO guilds(id, name, icon, updated) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, icon=excluded.icon, "
                "updated=excluded.updated",
                (gid, name, icon or "", time.time()),
            )
            self._cache.pop(gid, None)

    @staticmethod
    def _row_to_guild(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "icon": row["icon"],
            "modules": json.loads(row["modules"]),
            "config": json.loads(row["config"]),
        }

    def list_guilds(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM guilds ORDER BY name COLLATE NOCASE").fetchall()
        return [self._row_to_guild(r) for r in rows]

    def get_guild(self, gid: str) -> dict | None:
        """Cached read; returns a copy the caller may modify."""
        with self._lock:
            if gid not in self._cache:
                row = self._db.execute("SELECT * FROM guilds WHERE id=?", (gid,)).fetchone()
                if row is None:
                    return None
                self._cache[gid] = self._row_to_guild(row)
            return copy.deepcopy(self._cache[gid])

    def module_enabled(self, gid: str, key: str) -> bool:
        with self._lock:
            guild = self._cache.get(gid) or self.get_guild(gid)
            return bool(guild and guild["modules"].get(key))

    def raw_config(self, gid: str, key: str) -> dict:
        guild = self.get_guild(gid)
        return (guild or {}).get("config", {}).get(key, {})

    def set_module(self, gid: str, key: str, enabled: bool) -> dict:
        with self._lock:
            guild = self.get_guild(gid)
            if guild is None:
                raise KeyError(gid)
            guild["modules"][key] = bool(enabled)
            with self._db:
                self._db.execute(
                    "UPDATE guilds SET modules=?, updated=? WHERE id=?",
                    (json.dumps(guild["modules"]), time.time(), gid),
                )
            self._cache.pop(gid, None)
        return self.get_guild(gid)

    def set_config(self, gid: str, key: str, config: dict) -> dict:
        with self._lock:
            guild = self.get_guild(gid)
            if guild is None:
                raise KeyError(gid)
            guild["config"][key] = config
            with self._db:
                self._db.execute(
                    "UPDATE guilds SET config=?, updated=? WHERE id=?",
                    (json.dumps(guild["config"], ensure_ascii=False), time.time(), gid),
                )
            self._cache.pop(gid, None)
        return self.get_guild(gid)

    def guild_lang(self, gid: str) -> str:
        guild = self.get_guild(gid)
        return ((guild or {}).get("config", {}).get("_server") or {}).get("language", "he")

    def guild_custom(self, gid: str) -> dict:
        """This server's own bot texts, colors and emojis (Customize page)."""
        raw = ((self.get_guild(gid) or {}).get("config", {}).get("_custom") or {})
        return {"texts": dict(raw.get("texts") or {}), "colors": dict(raw.get("colors") or {}),
                "emojis": dict(raw.get("emojis") or {})}

    def guild_lang_obj(self, gid: str):
        """The server's bot language, carrying its custom texts/colors/emojis (see i18n.Lang)."""
        from .i18n import Lang

        custom = self.guild_custom(gid)
        return Lang(self.guild_lang(gid), custom["texts"], custom["colors"], custom["emojis"])

    def set_guild_lang(self, gid: str, lang: str) -> dict:
        from .catalog import MODULE_KEYS, strip_defaults

        config = (self.get_guild(gid) or {}).get("config", {})
        for key in [k for k in config if k in MODULE_KEYS]:  # default messages follow the new language
            cleaned = strip_defaults(key, config[key])
            if cleaned != config[key]:
                self.set_config(gid, key, cleaned)
        server = dict((config.get("_server") or {}))
        server["language"] = lang
        return self.set_config(gid, "_server", server)

    def guild_bot(self, gid: str) -> str:
        """The bot account chosen to run this server ("" = automatic)."""
        return (((self.get_guild(gid) or {}).get("config", {}).get("_server") or {}).get("bot") or "")

    def set_guild_bot(self, gid: str, account_id: str) -> dict:
        server = dict(((self.get_guild(gid) or {}).get("config", {}).get("_server") or {}))
        server["bot"] = account_id
        return self.set_config(gid, "_server", server)

    def delete_guild(self, gid: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM guilds WHERE id=?", (gid,))
            self._cache.pop(gid, None)

    def enabled_modules_anywhere(self) -> set[str]:
        enabled: set[str] = set()
        for guild in self.list_guilds():
            enabled.update(k for k, v in guild["modules"].items() if v)
        return enabled

    # ---- warnings --------------------------------------------------------
    def add_warning(self, gid: str, uid: str, user_name: str, mod_id: str, mod_name: str, reason: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO warnings(guild_id, user_id, user_name, moderator_id, moderator_name, reason, created) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (gid, uid, user_name, mod_id, mod_name, reason[:500], time.time()),
            )
            return int(cur.lastrowid)

    def count_warnings(self, gid: str, uid: str, since: float = 0) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM warnings WHERE guild_id=? AND user_id=? AND created>=?", (gid, uid, since)
            ).fetchone()
        return int(row["n"])

    def list_warnings(self, gid: str, uid: str | None = None, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM warnings WHERE guild_id=?", [gid]
        if uid:
            sql += " AND user_id=?"
            args.append(uid)
        sql += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def delete_warning(self, gid: str, wid: int) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM warnings WHERE guild_id=? AND id=?", (gid, wid)).rowcount > 0

    def clear_warnings(self, gid: str, uid: str) -> int:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (gid, uid)).rowcount

    # ---- temporary bans --------------------------------------------------
    def add_tempban(self, gid: str, uid: str, until: float) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO tempbans(guild_id, user_id, until) VALUES(?, ?, ?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET until=excluded.until",
                (gid, uid, until),
            )

    def due_tempbans(self, now: float) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM tempbans WHERE until<=?", (now,)).fetchall()
        return [dict(r) for r in rows]

    def remove_tempban(self, gid: str, uid: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM tempbans WHERE guild_id=? AND user_id=?", (gid, uid))

    # ---- levels / XP -----------------------------------------------------
    def get_xp(self, gid: str, uid: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM xp WHERE guild_id=? AND user_id=?", (gid, uid)).fetchone()
        return dict(row) if row else {"guild_id": gid, "user_id": uid, "user_name": "", "xp": 0, "messages": 0, "last_xp": 0}

    def gain_xp(self, gid: str, uid: str, name: str, amount: int, now: float, cooldown: float) -> tuple[int, int] | None:
        """Adds message XP unless the user is on cooldown. Returns (old_xp, new_xp) or None."""
        with self._lock, self._db:
            row = self._db.execute("SELECT xp, last_xp FROM xp WHERE guild_id=? AND user_id=?", (gid, uid)).fetchone()
            if row and now - row["last_xp"] < cooldown:
                return None
            old = row["xp"] if row else 0
            self._db.execute(
                "INSERT INTO xp(guild_id, user_id, user_name, xp, messages, last_xp) VALUES(?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=xp+?, messages=messages+1, last_xp=?, user_name=?",
                (gid, uid, name, amount, now, amount, now, name),
            )
            return old, old + amount

    def change_xp(self, gid: str, uid: str, name: str, delta: int) -> tuple[int, int]:
        """Manual give/remove. XP never goes below zero. Returns (old_xp, new_xp)."""
        with self._lock, self._db:
            row = self._db.execute("SELECT xp FROM xp WHERE guild_id=? AND user_id=?", (gid, uid)).fetchone()
            old = row["xp"] if row else 0
            new = max(0, old + delta)
            self._db.execute(
                "INSERT INTO xp(guild_id, user_id, user_name, xp) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=?, user_name=CASE WHEN ?<>'' THEN ? ELSE user_name END",
                (gid, uid, name, new, new, name, name),
            )
            return old, new

    def rank_of(self, gid: str, uid: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) + 1 AS r FROM xp WHERE guild_id=? AND xp > (SELECT COALESCE(MAX(xp), 0) FROM xp "
                "WHERE guild_id=? AND user_id=?)", (gid, gid, uid)).fetchone()
        return int(row["r"])

    def leaderboard(self, gid: str, limit: int = 10, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM xp WHERE guild_id=? AND xp > 0 ORDER BY xp DESC, user_id LIMIT ? OFFSET ?",
                (gid, limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def reset_xp(self, gid: str, uid: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM xp WHERE guild_id=? AND user_id=?", (gid, uid)).rowcount > 0

    # ---- economy ---------------------------------------------------------
    def _ensure_wallet(self, gid: str, uid: str, name: str, start: int) -> dict:
        """Caller holds the lock. Creates the wallet with the starting balance on first use."""
        row = self._db.execute("SELECT * FROM wallets WHERE guild_id=? AND user_id=?", (gid, uid)).fetchone()
        if row is None:
            self._db.execute("INSERT INTO wallets(guild_id, user_id, user_name, balance) VALUES(?, ?, ?, ?)",
                             (gid, uid, name, start))
            return {"guild_id": gid, "user_id": uid, "user_name": name, "balance": start, "last_daily": 0, "streak": 0}
        if name and name != row["user_name"]:
            self._db.execute("UPDATE wallets SET user_name=? WHERE guild_id=? AND user_id=?", (name, gid, uid))
        return dict(row)

    def wallet(self, gid: str, uid: str, name: str = "", start: int = 0) -> dict:
        with self._lock, self._db:
            return self._ensure_wallet(gid, uid, name, start)

    def add_coins(self, gid: str, uid: str, name: str, delta: int, start: int = 0,
                  allow_negative: bool = False) -> int | None:
        """Changes a balance. Returns the new balance, or None if it would go below zero."""
        with self._lock, self._db:
            w = self._ensure_wallet(gid, uid, name, start)
            new = w["balance"] + delta
            if new < 0 and not allow_negative:
                return None
            self._db.execute("UPDATE wallets SET balance=? WHERE guild_id=? AND user_id=?", (new, gid, uid))
            return new

    def transfer(self, gid: str, src: str, src_name: str, dst: str, dst_name: str, amount: int, start: int = 0) -> bool:
        with self._lock, self._db:
            a = self._ensure_wallet(gid, src, src_name, start)
            self._ensure_wallet(gid, dst, dst_name, start)
            if amount <= 0 or a["balance"] < amount:
                return False
            self._db.execute("UPDATE wallets SET balance=balance-? WHERE guild_id=? AND user_id=?", (amount, gid, src))
            self._db.execute("UPDATE wallets SET balance=balance+? WHERE guild_id=? AND user_id=?", (amount, gid, dst))
            return True

    def claim_daily(self, gid: str, uid: str, name: str, now: float, streak: int, amount: int, start: int = 0) -> int:
        with self._lock, self._db:
            self._ensure_wallet(gid, uid, name, start)
            self._db.execute("UPDATE wallets SET balance=balance+?, last_daily=?, streak=? WHERE guild_id=? AND user_id=?",
                             (amount, now, streak, gid, uid))
            return self._db.execute("SELECT balance FROM wallets WHERE guild_id=? AND user_id=?", (gid, uid)).fetchone()[0]

    def rich_list(self, gid: str, limit: int = 10, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM wallets WHERE guild_id=? ORDER BY balance DESC, user_id LIMIT ? OFFSET ?",
                                    (gid, limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def reset_wallet(self, gid: str, uid: str) -> bool:
        with self._lock, self._db:
            n = self._db.execute("DELETE FROM wallets WHERE guild_id=? AND user_id=?", (gid, uid)).rowcount
            self._db.execute("DELETE FROM inventory WHERE guild_id=? AND user_id=?", (gid, uid))
            return n > 0

    def inventory(self, gid: str, uid: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT item, qty FROM inventory WHERE guild_id=? AND user_id=? AND qty>0 ORDER BY item",
                                    (gid, uid)).fetchall()
        return [dict(r) for r in rows]

    def item_sold(self, gid: str, item: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT sold FROM item_sales WHERE guild_id=? AND item=?", (gid, item)).fetchone()
        return row["sold"] if row else 0

    def buy_item(self, gid: str, uid: str, name: str, item: str, price: int, stock: int, per_user: int,
                 start: int = 0) -> tuple[str, int]:
        """Atomic purchase. Returns (status, balance): ok / sold_out / limit / funds."""
        with self._lock, self._db:
            w = self._ensure_wallet(gid, uid, name, start)
            sold = self._db.execute("SELECT sold FROM item_sales WHERE guild_id=? AND item=?", (gid, item)).fetchone()
            owned = self._db.execute("SELECT qty FROM inventory WHERE guild_id=? AND user_id=? AND item=?",
                                     (gid, uid, item)).fetchone()
            if stock and (sold[0] if sold else 0) >= stock:
                return "sold_out", w["balance"]
            if per_user and (owned[0] if owned else 0) >= per_user:
                return "limit", w["balance"]
            if w["balance"] < price:
                return "funds", w["balance"]
            self._db.execute("UPDATE wallets SET balance=balance-? WHERE guild_id=? AND user_id=?", (price, gid, uid))
            self._db.execute("INSERT INTO inventory(guild_id, user_id, item, qty) VALUES(?, ?, ?, 1) "
                             "ON CONFLICT(guild_id, user_id, item) DO UPDATE SET qty=qty+1", (gid, uid, item))
            self._db.execute("INSERT INTO item_sales(guild_id, item, sold) VALUES(?, ?, 1) "
                             "ON CONFLICT(guild_id, item) DO UPDATE SET sold=sold+1", (gid, item))
            return "ok", w["balance"] - price
