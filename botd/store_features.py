"""Tables and queries for the newer modules: activity, birthdays, reminders, giveaways, AFK, invites,
tickets, suggestions and starboard. Mixed into Store (same connection and lock)."""

import json
import time

FEATURE_TABLES = [
    """CREATE TABLE IF NOT EXISTS activity (
        guild_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL DEFAULT '',
        messages INTEGER NOT NULL DEFAULT 0, first REAL NOT NULL DEFAULT 0, last REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS birthdays (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT '',
        user_name TEXT NOT NULL DEFAULT '', month INTEGER NOT NULL, day INTEGER NOT NULL, year INTEGER NOT NULL DEFAULT 0,
        last_announced INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS birthdays_by_guild ON birthdays(guild_id, month, day)",
    """CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL DEFAULT '', channel_id TEXT NOT NULL,
        user_id TEXT NOT NULL, text TEXT NOT NULL, due REAL NOT NULL, created REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS reminders_due ON reminders(due)",
    """CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
        message_id TEXT NOT NULL DEFAULT '', prize TEXT NOT NULL, winners INTEGER NOT NULL DEFAULT 1,
        ends REAL NOT NULL, host_id TEXT NOT NULL DEFAULT '', ended INTEGER NOT NULL DEFAULT 0,
        winner_ids TEXT NOT NULL DEFAULT '[]', role_id TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS giveaway_entries (
        giveaway_id INTEGER NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (giveaway_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS afk (
        guild_id TEXT NOT NULL, user_id TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', since REAL NOT NULL,
        old_nick TEXT, PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS invites (
        guild_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL DEFAULT '',
        joins INTEGER NOT NULL DEFAULT 0, leaves INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS invite_joins (
        guild_id TEXT NOT NULL, member_id TEXT NOT NULL, inviter_id TEXT NOT NULL, PRIMARY KEY (guild_id, member_id))""",
    """CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, number INTEGER NOT NULL,
        thread_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL, closed REAL NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, number INTEGER NOT NULL,
        channel_id TEXT NOT NULL, message_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL,
        user_name TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
        note TEXT NOT NULL DEFAULT '', created REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS starboard (
        guild_id TEXT NOT NULL, message_id TEXT NOT NULL, star_message_id TEXT NOT NULL,
        PRIMARY KEY (guild_id, message_id))""",
]


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


class FeatureStoreMixin:
    _db = None
    _lock = None

    def _init_features(self) -> None:
        with self._lock, self._db:
            for sql in FEATURE_TABLES:
                self._db.execute(sql)

    # ---- activity (message counter used by Claude's "who talks the most") ----
    def count_message(self, gid: str, uid: str, name: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO activity (guild_id, user_id, user_name, messages, first, last) VALUES (?, ?, ?, 1, ?, ?)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET messages = messages + 1, user_name = excluded.user_name,
                   last = excluded.last""", (gid, uid, name, now, now))

    def top_talkers(self, gid: str, limit: int = 10) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute(
                "SELECT user_id, user_name, messages, first, last FROM activity WHERE guild_id = ? ORDER BY messages DESC LIMIT ?",
                (gid, limit)))

    def activity_since(self, gid: str) -> float:
        with self._lock:
            row = self._db.execute("SELECT MIN(first) AS f FROM activity WHERE guild_id = ?", (gid,)).fetchone()
        return float(row["f"] or 0)

    # ---- birthdays -------------------------------------------------------------
    def set_birthday(self, gid: str, uid: str, name: str, month: int, day: int, year: int = 0) -> int:
        """One birthday per member (or per name when the person is not a member). Returns the row id."""
        with self._lock, self._db:
            if uid:
                row = self._db.execute("SELECT id FROM birthdays WHERE guild_id = ? AND user_id = ?", (gid, uid)).fetchone()
            else:
                row = self._db.execute("SELECT id FROM birthdays WHERE guild_id = ? AND user_id = '' AND lower(user_name) = lower(?)",
                                       (gid, name)).fetchone()
            if row:
                self._db.execute("UPDATE birthdays SET user_name = ?, month = ?, day = ?, year = ?, last_announced = 0 WHERE id = ?",
                                 (name, month, day, year, row["id"]))
                return int(row["id"])
            cur = self._db.execute("INSERT INTO birthdays (guild_id, user_id, user_name, month, day, year) VALUES (?, ?, ?, ?, ?, ?)",
                                   (gid, uid, name, month, day, year))
            return int(cur.lastrowid)

    def list_birthdays(self, gid: str) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute("SELECT * FROM birthdays WHERE guild_id = ? ORDER BY month, day", (gid,)))

    def get_birthday(self, gid: str, uid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM birthdays WHERE guild_id = ? AND user_id = ?", (gid, uid)).fetchone()
        return dict(row) if row else None

    def delete_birthday(self, gid: str, bid: int) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM birthdays WHERE guild_id = ? AND id = ?", (gid, bid)).rowcount > 0

    def birthdays_on(self, gid: str, month: int, day: int) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute("SELECT * FROM birthdays WHERE guild_id = ? AND month = ? AND day = ?", (gid, month, day)))

    def mark_birthday_announced(self, bid: int, year: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE birthdays SET last_announced = ? WHERE id = ?", (year, bid))

    # ---- reminders -------------------------------------------------------------
    def add_reminder(self, gid: str, channel_id: str, uid: str, text: str, due: float) -> int:
        with self._lock, self._db:
            return int(self._db.execute(
                "INSERT INTO reminders (guild_id, channel_id, user_id, text, due, created) VALUES (?, ?, ?, ?, ?, ?)",
                (gid, channel_id, uid, text[:1000], due, time.time())).lastrowid)

    def due_reminders(self, now: float) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute("SELECT * FROM reminders WHERE due <= ? ORDER BY due LIMIT 50", (now,)))

    def list_reminders(self, gid: str = "", uid: str = "") -> list[dict]:
        sql, args = "SELECT * FROM reminders WHERE 1=1", []
        if gid:
            sql, args = sql + " AND guild_id = ?", args + [gid]
        if uid:
            sql, args = sql + " AND user_id = ?", args + [uid]
        with self._lock:
            return _rows(self._db.execute(sql + " ORDER BY due LIMIT 100", args))

    def delete_reminder(self, rid: int, uid: str = "", gid: str = "") -> bool:
        sql, args = "DELETE FROM reminders WHERE id = ?", [rid]
        if uid:
            sql, args = sql + " AND user_id = ?", args + [uid]
        if gid:
            sql, args = sql + " AND guild_id = ?", args + [gid]
        with self._lock, self._db:
            return self._db.execute(sql, args).rowcount > 0

    # ---- giveaways -------------------------------------------------------------
    def add_giveaway(self, gid: str, channel_id: str, prize: str, winners: int, ends: float, host_id: str,
                     role_id: str = "") -> int:
        with self._lock, self._db:
            return int(self._db.execute(
                "INSERT INTO giveaways (guild_id, channel_id, prize, winners, ends, host_id, role_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (gid, channel_id, prize[:200], winners, ends, host_id, role_id)).lastrowid)

    def set_giveaway_message(self, gw_id: int, message_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, gw_id))

    def get_giveaway(self, gw_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM giveaways WHERE id = ?", (gw_id,)).fetchone()
        return dict(row) if row else None

    def giveaway_by_message(self, message_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM giveaways WHERE message_id = ?", (message_id,)).fetchone()
        return dict(row) if row else None

    def list_giveaways(self, gid: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = _rows(self._db.execute(
                """SELECT g.*, (SELECT COUNT(*) FROM giveaway_entries e WHERE e.giveaway_id = g.id) AS entries
                   FROM giveaways g WHERE guild_id = ? ORDER BY ended, ends DESC LIMIT ?""", (gid, limit)))
        for r in rows:
            r["winner_ids"] = json.loads(r["winner_ids"] or "[]")
        return rows

    def due_giveaways(self, now: float) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute("SELECT * FROM giveaways WHERE ended = 0 AND ends <= ?", (now,)))

    def toggle_entry(self, gw_id: int, uid: str) -> bool:
        """Adds the user to the giveaway, or removes them if already in. Returns True if now entered."""
        with self._lock, self._db:
            if self._db.execute("DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (gw_id, uid)).rowcount:
                return False
            self._db.execute("INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)", (gw_id, uid))
            return True

    def giveaway_entries(self, gw_id: int) -> list[str]:
        with self._lock:
            return [r["user_id"] for r in self._db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (gw_id,))]

    def end_giveaway(self, gw_id: int, winner_ids: list[str]) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE giveaways SET ended = 1, winner_ids = ? WHERE id = ?", (json.dumps(winner_ids), gw_id))

    # ---- AFK -------------------------------------------------------------------
    def set_afk(self, gid: str, uid: str, reason: str, old_nick: str | None = None) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO afk (guild_id, user_id, reason, since, old_nick) VALUES (?, ?, ?, ?, ?)",
                             (gid, uid, reason[:300], time.time(), old_nick))

    def get_afk(self, gid: str, uid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM afk WHERE guild_id = ? AND user_id = ?", (gid, uid)).fetchone()
        return dict(row) if row else None

    def clear_afk(self, gid: str, uid: str) -> dict | None:
        row = self.get_afk(gid, uid)
        if row:
            with self._lock, self._db:
                self._db.execute("DELETE FROM afk WHERE guild_id = ? AND user_id = ?", (gid, uid))
        return row

    # ---- invite tracker --------------------------------------------------------
    def record_invite_join(self, gid: str, member_id: str, inviter_id: str, inviter_name: str) -> int:
        """Returns the inviter's total joins."""
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO invite_joins (guild_id, member_id, inviter_id) VALUES (?, ?, ?)",
                             (gid, member_id, inviter_id))
            self._db.execute(
                """INSERT INTO invites (guild_id, user_id, user_name, joins) VALUES (?, ?, ?, 1)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET joins = joins + 1, user_name = excluded.user_name""",
                (gid, inviter_id, inviter_name))
            row = self._db.execute("SELECT joins - leaves AS n FROM invites WHERE guild_id = ? AND user_id = ?",
                                   (gid, inviter_id)).fetchone()
        return int(row["n"])

    def record_invite_leave(self, gid: str, member_id: str) -> str | None:
        with self._lock, self._db:
            row = self._db.execute("SELECT inviter_id FROM invite_joins WHERE guild_id = ? AND member_id = ?",
                                   (gid, member_id)).fetchone()
            if not row:
                return None
            self._db.execute("DELETE FROM invite_joins WHERE guild_id = ? AND member_id = ?", (gid, member_id))
            self._db.execute("UPDATE invites SET leaves = leaves + 1 WHERE guild_id = ? AND user_id = ?", (gid, row["inviter_id"]))
        return row["inviter_id"]

    def invite_stats(self, gid: str, uid: str) -> dict:
        with self._lock:
            row = self._db.execute("SELECT joins, leaves FROM invites WHERE guild_id = ? AND user_id = ?", (gid, uid)).fetchone()
        joins, leaves = (row["joins"], row["leaves"]) if row else (0, 0)
        return {"joins": joins, "leaves": leaves, "total": joins - leaves}

    def invite_board(self, gid: str, limit: int = 10) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute(
                """SELECT user_id, user_name, joins, leaves, joins - leaves AS total FROM invites WHERE guild_id = ?
                   ORDER BY total DESC, joins DESC LIMIT ?""", (gid, limit)))

    # ---- tickets ---------------------------------------------------------------
    def open_ticket(self, gid: str, thread_id: str, uid: str, name: str, topic: str) -> int:
        with self._lock, self._db:
            row = self._db.execute("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM tickets WHERE guild_id = ?", (gid,)).fetchone()
            self._db.execute(
                "INSERT INTO tickets (guild_id, number, thread_id, user_id, user_name, topic, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (gid, row["n"], thread_id, uid, name, topic[:200], time.time()))
        return int(row["n"])

    def next_ticket_number(self, gid: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM tickets WHERE guild_id = ?", (gid,)).fetchone()
        return int(row["n"])

    def ticket_by_thread(self, thread_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tickets WHERE thread_id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None

    def open_tickets_of(self, gid: str, uid: str) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute("SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND closed = 0", (gid, uid)))

    def close_ticket(self, thread_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE tickets SET closed = ? WHERE thread_id = ?", (time.time(), thread_id))

    def list_tickets(self, gid: str, limit: int = 50) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute("SELECT * FROM tickets WHERE guild_id = ? ORDER BY closed != 0, id DESC LIMIT ?",
                                          (gid, limit)))

    # ---- suggestions -----------------------------------------------------------
    def add_suggestion(self, gid: str, channel_id: str, uid: str, name: str, text: str) -> tuple[int, int]:
        with self._lock, self._db:
            row = self._db.execute("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM suggestions WHERE guild_id = ?", (gid,)).fetchone()
            cur = self._db.execute(
                "INSERT INTO suggestions (guild_id, number, channel_id, user_id, user_name, text, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (gid, row["n"], channel_id, uid, name, text[:2000], time.time()))
        return int(cur.lastrowid), int(row["n"])

    def set_suggestion_message(self, sid: int, message_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE suggestions SET message_id = ? WHERE id = ?", (message_id, sid))

    def get_suggestion(self, gid: str, number: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM suggestions WHERE guild_id = ? AND number = ?", (gid, number)).fetchone()
        return dict(row) if row else None

    def set_suggestion_status(self, gid: str, number: int, status: str, note: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE suggestions SET status = ?, note = ? WHERE guild_id = ? AND number = ?",
                             (status, note[:500], gid, number))

    def list_suggestions(self, gid: str, limit: int = 50) -> list[dict]:
        with self._lock:
            return _rows(self._db.execute("SELECT * FROM suggestions WHERE guild_id = ? ORDER BY id DESC LIMIT ?", (gid, limit)))

    # ---- starboard -------------------------------------------------------------
    def star_entry(self, gid: str, message_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT star_message_id FROM starboard WHERE guild_id = ? AND message_id = ?",
                                   (gid, message_id)).fetchone()
        return row["star_message_id"] if row else None

    def set_star_entry(self, gid: str, message_id: str, star_message_id: str | None) -> None:
        with self._lock, self._db:
            if star_message_id:
                self._db.execute("INSERT OR REPLACE INTO starboard (guild_id, message_id, star_message_id) VALUES (?, ?, ?)",
                                 (gid, message_id, star_message_id))
            else:
                self._db.execute("DELETE FROM starboard WHERE guild_id = ? AND message_id = ?", (gid, message_id))
