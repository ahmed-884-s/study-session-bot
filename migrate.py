"""
migrate.py — حوّل الداتا القديمة من studybot_data.json لـ SQLite

الاستخدام:
    python migrate.py [مسار الـ JSON القديم]

مثال:
    python migrate.py /tmp/studybot_data.json
"""

import sys
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager

# ── Config ─────────────────────────────────────────────────────────────────
DEFAULT_JSON = Path("/tmp/studybot_data.json")
DEFAULT_DB   = Path("/tmp/studybot.db")

# ── DB helpers ──────────────────────────────────────────────────────────────
@contextmanager
def get_db(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path):
    with get_db(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stats (
                user_id             TEXT PRIMARY KEY,
                name                TEXT DEFAULT '',
                username            TEXT DEFAULT '',
                total_minutes       INTEGER DEFAULT 0,
                sessions_completed  INTEGER DEFAULT 0,
                sessions_joined     INTEGER DEFAULT 0,
                last_study_date     TEXT
            );

            CREATE TABLE IF NOT EXISTS streaks (
                user_id   TEXT PRIMARY KEY,
                streak    INTEGER DEFAULT 0,
                last_date TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                chat_id TEXT PRIMARY KEY,
                data    TEXT NOT NULL
            );
        """)


# ── Migration ───────────────────────────────────────────────────────────────
def migrate(json_path: Path, db_path: Path):
    if not json_path.exists():
        print(f"❌ الملف مش موجود: {json_path}")
        sys.exit(1)

    print(f"📂 بقرأ: {json_path}")
    old = json.loads(json_path.read_text(encoding="utf-8"))

    init_db(db_path)

    stats_count   = 0
    streaks_count = 0
    sessions_count = 0

    with get_db(db_path) as conn:

        # ── stats ──────────────────────────────────────────────────────────
        for uid, s in old.get("stats", {}).items():
            conn.execute("""
                INSERT INTO stats
                    (user_id, name, username, total_minutes, sessions_completed, sessions_joined, last_study_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    name=excluded.name,
                    username=excluded.username,
                    total_minutes=excluded.total_minutes,
                    sessions_completed=excluded.sessions_completed,
                    sessions_joined=excluded.sessions_joined,
                    last_study_date=excluded.last_study_date
            """, (
                str(uid),
                s.get("name", ""),
                s.get("username", ""),
                s.get("total_minutes", 0),
                s.get("sessions_completed", 0),
                s.get("sessions_joined", 0),
                s.get("last_study_date"),
            ))
            stats_count += 1

        # ── streaks ────────────────────────────────────────────────────────
        for uid, s in old.get("streaks", {}).items():
            conn.execute("""
                INSERT INTO streaks (user_id, streak, last_date)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    streak=excluded.streak,
                    last_date=excluded.last_date
            """, (
                str(uid),
                s.get("streak", 0),
                s.get("last_date"),
            ))
            streaks_count += 1

        # ── sessions ───────────────────────────────────────────────────────
        for chat_id, session in old.get("sessions", {}).items():
            conn.execute("""
                INSERT INTO sessions (chat_id, data)
                VALUES (?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET data=excluded.data
            """, (
                str(chat_id),
                json.dumps(session, ensure_ascii=False),
            ))
            sessions_count += 1

    print(f"✅ اتنقل بنجاح إلى: {db_path}")
    print(f"   👤 مستخدمين (stats):   {stats_count}")
    print(f"   🔥 streaks:             {streaks_count}")
    print(f"   📚 sessions:            {sessions_count}")
    print()
    print("دلوقتي تقدر تشتغل بـ bot.py بشكل طبيعي والداتا موجودة.")


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSON
    db_path   = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DB
    migrate(json_path, db_path)
