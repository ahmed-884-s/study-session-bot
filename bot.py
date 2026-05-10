import os
import asyncio
import logging
import json
import re
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TZ = ZoneInfo("Africa/Cairo")

DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "studybot.db"

# ── Permissions ────────────────────────────────────────────────────────────
LOCKED = ChatPermissions(can_send_messages=False)
UNLOCKED = ChatPermissions(
    can_send_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
)

# ── Messages ───────────────────────────────────────────────────────────────
MOTIVATIONAL = [
    "⚡ استمر — كل دقيقة بتفرق!",
    "📖 أنت بتبني مستقبلك دلوقتي. متوقفش!",
    "🔥 الاستمرارية أقوى من الاندفاع. فضل مركّز!",
    "🧠 دماغك بتتقوى مع كل صفحة بتقراها.",
    "💪 الأبطال بيتصنعوا في لحظات زي دي.",
    "🌟 ساعة أقرب للهدف. أنت قادر!",
    "🎯 ركّز. خد نفس. كمّل.",
    "🚀 المجهود اللي بتبذله النهارده هيرجع عليك بكره.",
    "📚 كل خبير كان في الأول مبتدئ. كمّل ادفع!",
    "⏰ الوقت بيجري — خليه يجري في الصح!",
]

BREAK_OVER = [
    "☕ خلص الراحة! وقت المذاكرة تاني. 📚",
    "⏰ انتهى وقت الراحة — يلا شغل يا بطل! 💪",
    "🔔 الراحة خلصت! مستقبلك هيشكرك. 🚀",
    "📚 اتشحنت — يلا نرجع نتقفل على المذاكرة! 🔒",
]

GUARD_MESSAGES = [
    "📵 {mention} مذاكرة مذاكرة يسطا! 🔒",
    "🚫 {mention} إيه الكلام ده في وقت المذاكرة! 📚",
    "⚠️ {mention} ركّز يا بطل! المذاكرة أولاً 🎯",
    "🔕 {mention} مش وقت كلام دلوقتي، ركّز! 📖",
]

# ── SQLite Database ────────────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stats (
                user_id     TEXT PRIMARY KEY,
                name        TEXT DEFAULT '',
                username    TEXT DEFAULT '',
                total_minutes   INTEGER DEFAULT 0,
                sessions_completed INTEGER DEFAULT 0,
                sessions_joined    INTEGER DEFAULT 0,
                last_study_date    TEXT
            );

            CREATE TABLE IF NOT EXISTS streaks (
                user_id   TEXT PRIMARY KEY,
                streak    INTEGER DEFAULT 0,
                last_date TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                chat_id     TEXT PRIMARY KEY,
                data        TEXT NOT NULL
            );
        """)
    logger.info(f"✅ Database initialized at {DB_PATH}")


# ── Session helpers (stored as JSON blob in SQLite) ────────────────────────
def load_session(chat_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT data FROM sessions WHERE chat_id=?", (chat_id,)).fetchone()
    if row:
        return json.loads(row["data"])
    return None


def save_session(chat_id: str, session: dict):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (chat_id, data) VALUES (?, ?)",
            (chat_id, json.dumps(session, default=str, ensure_ascii=False))
        )


def delete_session(chat_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE chat_id=?", (chat_id,))


# ── Stats helpers ──────────────────────────────────────────────────────────
def get_stats(user_id: str) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM stats WHERE user_id=?", (str(user_id),)).fetchone()
        if row:
            return dict(row)
        # Create default row
        conn.execute(
            "INSERT INTO stats (user_id) VALUES (?)",
            (str(user_id),)
        )
    return {
        "user_id": str(user_id), "name": "", "username": "",
        "total_minutes": 0, "sessions_completed": 0,
        "sessions_joined": 0, "last_study_date": None,
    }


def save_stats(s: dict):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO stats (user_id, name, username, total_minutes, sessions_completed, sessions_joined, last_study_date)
            VALUES (:user_id, :name, :username, :total_minutes, :sessions_completed, :sessions_joined, :last_study_date)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                username=excluded.username,
                total_minutes=excluded.total_minutes,
                sessions_completed=excluded.sessions_completed,
                sessions_joined=excluded.sessions_joined,
                last_study_date=excluded.last_study_date
        """, s)


def get_streak(user_id: str) -> int:
    with get_db() as conn:
        row = conn.execute("SELECT streak FROM streaks WHERE user_id=?", (str(user_id),)).fetchone()
    return row["streak"] if row else 0


def update_streak(user_id: str):
    uid = str(user_id)
    today = now().date().isoformat()
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    with get_db() as conn:
        row = conn.execute("SELECT streak, last_date FROM streaks WHERE user_id=?", (uid,)).fetchone()
        if row:
            if row["last_date"] == today:
                return
            new_streak = (row["streak"] + 1) if row["last_date"] == yesterday else 1
            conn.execute(
                "UPDATE streaks SET streak=?, last_date=? WHERE user_id=?",
                (new_streak, today, uid)
            )
        else:
            conn.execute(
                "INSERT INTO streaks (user_id, streak, last_date) VALUES (?, 1, ?)",
                (uid, today)
            )


def get_leaderboard(limit: int = 10) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT user_id, name, username, total_minutes FROM stats ORDER BY total_minutes DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]

# ── Utility functions ──────────────────────────────────────────────────────
def now() -> datetime:
    return datetime.now(TZ)


def fmt_time(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(str(dt_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.strftime("%I:%M %p")
    except Exception:
        return str(dt_str)


def fmt_duration(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}س {m}د"
    elif h:
        return f"{h}س"
    return f"{m}د"


def parse_duration(text: str) -> int | None:
    text = text.lower().strip()
    match = re.fullmatch(r'(?:(\d+)\s*h)?\s*(?:(\d+)\s*m?)?', text)
    if not match:
        return None
    h_str, m_str = match.group(1), match.group(2)
    if not h_str and not m_str:
        return None
    h = int(h_str) if h_str else 0
    m = int(m_str) if m_str else 0
    total = h * 60 + m
    return total if 10 <= total <= 480 else None


def mention(name: str, user_id: int | str) -> str:
    return f"[{name}](tg://user?id={user_id})"


def is_session_admin(user_id: int | str, session: dict) -> bool:
    return str(user_id) == str(session.get("started_by"))


def cancel_jobs(context: ContextTypes.DEFAULT_TYPE, *names: str):
    for name in names:
        for job in context.job_queue.get_jobs_by_name(name):
            job.schedule_removal()

# ── Chat permission helpers ────────────────────────────────────────────────
async def lock_chat(bot, chat_id: int):
    try:
        await bot.set_chat_permissions(chat_id, LOCKED)
    except TelegramError as e:
        logger.warning(f"Lock chat failed ({chat_id}): {e}")


async def unlock_chat(bot, chat_id: int):
    try:
        await bot.set_chat_permissions(chat_id, UNLOCKED)
    except TelegramError as e:
        logger.warning(f"Unlock chat failed ({chat_id}): {e}")


async def restrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, LOCKED)
    except TelegramError as e:
        logger.warning(f"Restrict user {user_id} failed: {e}")


async def unrestrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, UNLOCKED)
    except TelegramError as e:
        logger.warning(f"Unrestrict user {user_id} failed: {e}")

# ── /start ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *StudyLock Bot*\n\n"
        "بساعد مجموعات المذاكرة تتركز عن طريق قفل الشات أثناء الجلسات.\n\n"
        "*الأوامر:*\n"
        "`/study 2h` — ابدأ جلسة ساعتين\n"
        "`/study 90m` — ابدأ جلسة 90 دقيقة\n"
        "`/pomodoro [عدد]` — ابدأ جلسة بوموڊورو\n"
        "`/break 10` — خد استراحة 10 دقايق\n"
        "`/back` — خلّص الاستراحة بدري\n"
        "`/status` — شوف حالة الجلسة الحالية\n"
        "`/stats` — إحصائياتك الشخصية\n"
        "`/leaderboard` — ترتيب المجموعة\n"
        "`/end` — اخرج من الجلسة\n"
        "`/cancel` — إلغاء الجلسة *(للأدمن فقط)*\n\n"
        "_ضيفني للجروب واديني صلاحية أدمن عشان أشتغل!_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /study ─────────────────────────────────────────────────────────────────
async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    session = load_session(chat_id)

    if session and session.get("state") in ("waiting", "active"):
        await update.message.reply_text("⚠️ في جلسة مذاكرة شغّالة دلوقتي!\nاستخدم /status تشوف تفاصيلها.")
        return

    if not context.args:
        await update.message.reply_text(
            "📚 *الاستخدام:* `/study <المدة>`\n\n"
            "أمثلة:\n"
            "`/study 2h` — ساعتين\n"
            "`/study 90m` — 90 دقيقة\n"
            "`/study 1h30m` — ساعة ونص\n\n"
            "الحد الأدنى: 10 دقايق | الحد الأقصى: 8 ساعات",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    duration = parse_duration(context.args[0])
    if not duration:
        await update.message.reply_text(
            "❌ المدة غلط. استخدم صيغة زي `2h` أو `90m` أو `1h30m`.\n"
            "الحد الأدنى: 10 دقايق | الحد الأقصى: 8 ساعات.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await _create_session(update, context, chat_id, user, duration, pomodoro=False)


# ── /pomodoro ──────────────────────────────────────────────────────────────
async def cmd_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    session = load_session(chat_id)

    if session and session.get("state") in ("waiting", "active"):
        await update.message.reply_text("⚠️ في جلسة شغّالة دلوقتي.")
        return

    cycles = 4
    if context.args and context.args[0].isdigit():
        cycles = max(1, min(int(context.args[0]), 8))

    work_min, break_min = 25, 5
    total = cycles * (work_min + break_min)

    await _create_session(
        update, context, chat_id, user, total,
        pomodoro=True, pomo_cycles=cycles, pomo_work=work_min, pomo_break=break_min,
    )


# ── Shared session creation ────────────────────────────────────────────────
async def _create_session(
    update, context, chat_id, user, duration, *,
    pomodoro=False, pomo_cycles=4, pomo_work=25, pomo_break=5,
):
    join_deadline = (now() + timedelta(minutes=5)).isoformat()

    session = {
        "state": "waiting",
        "duration": duration,
        "started_by": user.id,
        "participants": {
            str(user.id): {"name": user.full_name, "username": user.username or ""}
        },
        "join_deadline": join_deadline,
        "start_time": None,
        "end_time": None,
        "breaks": {},
        "pomodoro": pomodoro,
        "pomo_cycles": pomo_cycles if pomodoro else 0,
        "pomo_work": pomo_work if pomodoro else 0,
        "pomo_break": pomo_break if pomodoro else 0,
    }
    save_session(chat_id, session)

    s = get_stats(str(user.id))
    s["username"] = user.username or ""
    s["name"] = user.full_name
    s["sessions_joined"] += 1
    save_stats(s)

    if pomodoro:
        title = "🍅 *جلسة بوموڊورو!*"
        details = (
            f"🔄 الدورات: *{pomo_cycles}* × (25د مذاكرة + 5د راحة)\n"
            f"⏱ الإجمالي: *{fmt_duration(duration)}*"
        )
        btn_label = "✋ انضم للبوموڊورو"
    else:
        title = "📚 *جلسة مذاكرة جديدة!*"
        details = f"⏱ المدة: *{fmt_duration(duration)}*"
        btn_label = "✋ انضم للجلسة"

    keyboard = [[InlineKeyboardButton(btn_label, callback_data=f"join_{chat_id}")]]

    await update.message.reply_text(
        f"{title}\n\n"
        f"👤 بدأها: {user.full_name}\n"
        f"{details}\n"
        f"👥 المشتركين حتى دلوقتي: 1\n\n"
        f"⏳ *عندك 5 دقايق للانضمام!*\n"
        f"الجلسة بتبدأ الساعة: *{fmt_time(join_deadline)}*\n\n"
        f"اضغط الزرار اللي تحت عشان تنضم 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.job_queue.run_once(
        start_session_job,
        when=300,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}",
    )


# ── Join button callback ───────────────────────────────────────────────────
async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    _, chat_id = query.data.split("_", 1)
    session = load_session(chat_id)

    if not session or session["state"] != "waiting":
        await query.answer("❌ الجلسة دي مش متاحة للانضمام.", show_alert=True)
        return

    uid = str(user.id)
    if uid in session["participants"]:
        await query.answer("✅ أنت منضم أصلاً!", show_alert=True)
        return

    session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
    save_session(chat_id, session)

    s = get_stats(uid)
    s["username"] = user.username or ""
    s["name"] = user.full_name
    s["sessions_joined"] += 1
    save_stats(s)

    names = [p["name"] for p in session["participants"].values()]
    await query.answer("✅ انضممت للجلسة!", show_alert=True)

    btn_label = "✋ انضم للبوموڊورو" if session.get("pomodoro") else "✋ انضم للجلسة"
    keyboard = [[InlineKeyboardButton(btn_label, callback_data=f"join_{chat_id}")]]

    await query.edit_message_text(
        f"📚 *جلسة مذاكرة — غرفة الانتظار*\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"👥 المشتركين ({len(names)}): {', '.join(names)}\n\n"
        f"⏳ الجلسة بتبدأ خلال أقل من 5 دقايق!\n"
        f"اضغط الزرار عشان تنضم 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Start session job ──────────────────────────────────────────────────────
async def start_session_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    chat_int = job_data["chat_int"]

    session = load_session(chat_id)
    if not session or session["state"] != "waiting":
        return

    start = now()
    end = start + timedelta(minutes=session["duration"])
    session["state"] = "active"
    session["start_time"] = start.isoformat()
    session["end_time"] = end.isoformat()
    save_session(chat_id, session)

    names = [p["name"] for p in session["participants"].values()]
    await lock_chat(context.bot, chat_int)

    pomo_note = ""
    if session.get("pomodoro"):
        pomo_note = f"\n🍅 وضع بوموڊورو: {session['pomo_cycles']} دورة — 25د مذاكرة + 5د راحة"

    await context.bot.send_message(
        chat_int,
        f"🔒 *الجلسة بدأت!*\n\n"
        f"👥 المشتركين: {', '.join(names)}\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"🏁 بتخلص الساعة: *{fmt_time(end.isoformat())}*"
        f"{pomo_note}\n\n"
        f"📵 *الشات اتقفل.* وضع التركيز شغّال!\n"
        f"_استخدم /break <دقايق> لو محتاج استراحة._",
        parse_mode=ParseMode.MARKDOWN,
    )

    total_hours = session["duration"] // 60
    for i in range(1, total_hours + 1):
        elapsed_min = i * 60
        remaining_min = session["duration"] - elapsed_min
        context.job_queue.run_once(
            send_motivation_job,
            when=elapsed_min * 60,
            data={"chat_int": chat_int, "elapsed_min": elapsed_min, "remaining_min": remaining_min},
            name=f"motiv_{chat_id}_{i}",
        )

    context.job_queue.run_once(
        end_session_job,
        when=session["duration"] * 60,
        data={"chat_id": chat_id, "chat_int": chat_int},
        name=f"end_{chat_id}",
    )


# ── Motivational job ───────────────────────────────────────────────────────
async def send_motivation_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    msg = random.choice(MOTIVATIONAL)
    elapsed = fmt_duration(d["elapsed_min"])
    remaining = fmt_duration(d["remaining_min"])
    await context.bot.send_message(
        d["chat_int"],
        f"{msg}\n\n⏱ مضى *{elapsed}* · باقي *{remaining}*",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── End session job ────────────────────────────────────────────────────────
async def end_session_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    chat_int = job_data["chat_int"]

    session = load_session(chat_id)
    if not session:
        return

    lines = []
    for uid, pinfo in session["participants"].items():
        s = get_stats(uid)
        s["total_minutes"] += session["duration"]
        s["sessions_completed"] += 1
        s["last_study_date"] = now().date().isoformat()
        save_stats(s)
        update_streak(uid)
        streak = get_streak(uid)
        streak_str = f" 🔥 {streak}" if streak > 1 else ""
        lines.append(f" • {pinfo['name']} — {fmt_duration(session['duration'])}{streak_str}")

    session["state"] = "ended"
    save_session(chat_id, session)
    await unlock_chat(context.bot, chat_int)

    await context.bot.send_message(
        chat_int,
        f"✅ *الجلسة خلصت!*\n\n"
        f"🎉 أنتم عمالقة!\n"
        f"⏱ إجمالي وقت المذاكرة: *{fmt_duration(session['duration'])}*\n\n"
        f"*المشتركين:*\n" + "\n".join(lines) + "\n\n"
        f"💤 الشات *اتفتح*. استاهلتوا استراحة!\n"
        f"_استخدم /break <دقايق> عشان تبدأ مؤقت استراحة._\n"
        f"_استخدم /stats عشان تشوف إحصائياتك._",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /break ─────────────────────────────────────────────────────────────────
async def cmd_break(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)
    session = load_session(chat_id)

    if not session or session["state"] not in ("ended", "active"):
        await update.message.reply_text("❌ مفيش جلسة حديثة. ابدأ واحدة بـ /study.")
        return
    if uid not in session["participants"]:
        await update.message.reply_text("❌ أنت مكنتش في الجلسة دي.")
        return
    if uid in session.get("breaks", {}):
        await update.message.reply_text("⚠️ أنت في استراحة أصلاً! استخدم /back لو عاوز ترجع بدري.")
        return

    if not context.args:
        await update.message.reply_text(
            "☕ *الاستخدام:* `/break <دقايق>`\n\nمثال: `/break 10` أو `/break 15`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        minutes = int(context.args[0])
        if not 1 <= minutes <= 60:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ مدة الاستراحة لازم تكون بين 1 و60 دقيقة.")
        return

    break_end = now() + timedelta(minutes=minutes)
    session.setdefault("breaks", {})[uid] = {
        "end": break_end.isoformat(),
        "duration": minutes,
        "name": user.full_name,
    }
    save_session(chat_id, session)

    await restrict_user(context.bot, update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"☕ *بدأت استراحة {user.full_name}*\n\n"
        f"⏱ المدة: *{minutes} دقيقة*\n"
        f"🔔 هترجع الساعة: *{fmt_time(break_end.isoformat())}*\n\n"
        f"_استريح واتشحن — الشات مقفول عليك لحد إمتى!_ 😴",
        parse_mode=ParseMode.MARKDOWN,
    )

    context.job_queue.run_once(
        end_break_job,
        when=minutes * 60,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id, "uid": uid, "name": user.full_name},
        name=f"break_{chat_id}_{uid}",
    )


# ── End break job ──────────────────────────────────────────────────────────
async def end_break_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    chat_id, chat_int, uid, name = d["chat_id"], d["chat_int"], d["uid"], d["name"]

    session = load_session(chat_id)
    if session and uid in session.get("breaks", {}):
        del session["breaks"][uid]
        save_session(chat_id, session)

    await unrestrict_user(context.bot, chat_int, int(uid))

    ref = mention(name, uid)
    await context.bot.send_message(
        chat_int,
        f"{random.choice(BREAK_OVER)}\n\n{ref}، استراحتك خلصت — ارجع للمذاكرة! 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

    session = load_session(chat_id)
    if session and session.get("state") == "active":
        await restrict_user(context.bot, chat_int, int(uid))
        await context.bot.send_message(
            chat_int,
            f"🔒 {ref} رجع لوضع التركيز!",
            parse_mode=ParseMode.MARKDOWN,
        )


# ── /back ──────────────────────────────────────────────────────────────────
async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)
    session = load_session(chat_id)

    if not session or uid not in session.get("breaks", {}):
        await update.message.reply_text("❌ مفيش استراحة شغّالة عندك.")
        return

    cancel_jobs(context, f"break_{chat_id}_{uid}")
    del session["breaks"][uid]
    save_session(chat_id, session)

    await unrestrict_user(context.bot, update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"💪 *{user.full_name}* خلّص استراحته بدري!\nرجع للمذاكرة 📚🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

    if session.get("state") == "active":
        await restrict_user(context.bot, update.effective_chat.id, user.id)
        await update.message.reply_text(
            f"🔒 {mention(user.full_name, user.id)} رجع لوضع التركيز!",
            parse_mode=ParseMode.MARKDOWN,
        )


# ── /end ───────────────────────────────────────────────────────────────────
async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)
    session = load_session(chat_id)

    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ مفيش جلسة نشطة تخرج منها.")
        return
    if uid not in session["participants"]:
        await update.message.reply_text("❌ أنت مش من ضمن المشتركين في الجلسة دي.")
        return

    del session["participants"][uid]
    if session["state"] == "active":
        await unrestrict_user(context.bot, update.effective_chat.id, user.id)

    cancel_jobs(context, f"break_{chat_id}_{uid}")
    session.get("breaks", {}).pop(uid, None)
    save_session(chat_id, session)

    await update.message.reply_text(
        f"👋 *{user.full_name}* خرج من الجلسة.\n"
        f"_تذكر: الاستمرارية هي المفتاح! شوفك المرة الجاية._ 📚",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /cancel ────────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)
    session = load_session(chat_id)

    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ مفيش جلسة نشطة تلغيها.")
        return

    if not is_session_admin(uid, session):
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("❌ الأمر ده للي بدأ الجلسة أو أدمن الجروب بس.")
                return
        except TelegramError:
            await update.message.reply_text("❌ الأمر ده للي بدأ الجلسة أو أدمن الجروب بس.")
            return

    total_hours = session.get("duration", 0) // 60
    job_names = [f"start_{chat_id}", f"end_{chat_id}"]
    job_names += [f"motiv_{chat_id}_{i}" for i in range(1, total_hours + 1)]
    for uid_p in session.get("participants", {}):
        job_names.append(f"break_{chat_id}_{uid_p}")
    cancel_jobs(context, *job_names)

    for uid_p in session.get("participants", {}):
        await unrestrict_user(context.bot, update.effective_chat.id, int(uid_p))

    session["state"] = "cancelled"
    save_session(chat_id, session)
    await unlock_chat(context.bot, update.effective_chat.id)

    await update.message.reply_text(
        f"🛑 *الجلسة اتلغت* بواسطة {user.full_name}.\n"
        f"الشات اتفتح دلوقتي. ابدأ جلسة جديدة أي وقت!",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /status ────────────────────────────────────────────────────────────────
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    session = load_session(chat_id)

    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text(
            "💤 *مفيش جلسة نشطة دلوقتي.*\n\nابدأ واحدة بـ `/study <المدة>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    breaks = session.get("breaks", {})
    lines = []
    for uid, pinfo in session["participants"].items():
        if uid in breaks:
            end_str = fmt_time(breaks[uid]["end"])
            lines.append(f" ☕ {pinfo['name']} — في استراحة (بيرجع الساعة {end_str})")
        else:
            lines.append(f" 📖 {pinfo['name']} — بيذاكر")

    state_emoji = "⏳" if session["state"] == "waiting" else "🔒"
    state_text = "في الانتظار" if session["state"] == "waiting" else "شغّالة — الشات مقفول"
    pomo_note = "\n🍅 وضع بوموڊورو" if session.get("pomodoro") else ""

    msg = (
        f"{state_emoji} *حالة الجلسة: {state_text}*{pomo_note}\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
    )
    if session.get("end_time"):
        msg += f"🏁 بتخلص الساعة: *{fmt_time(session['end_time'])}*\n"
    msg += f"\n*المشتركين ({len(lines)}):*\n" + "\n".join(lines)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ── /stats ─────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    s = get_stats(uid)
    streak = get_streak(uid)
    h, m = divmod(s["total_minutes"], 60)

    await update.message.reply_text(
        f"📊 *إحصائياتك في المذاكرة*\n\n"
        f"⏱ إجمالي وقت المذاكرة: *{h}س {m}د*\n"
        f"✅ الجلسات المكتملة: *{s['sessions_completed']}*\n"
        f"👥 الجلسات اللي اشتركت فيها: *{s['sessions_joined']}*\n"
        f"🔥 الـ streak الحالي: *{streak} يوم*\n"
        f"📅 آخر يوم مذاكرة: *{s.get('last_study_date') or 'مفيش بيانات'}*",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /leaderboard ───────────────────────────────────────────────────────────
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_leaderboard(10)
    if not rows:
        await update.message.reply_text("📊 مفيش بيانات لسه! ابدأ جلسة مذاكرة عشان يبدأ التتبع.")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = []
    for i, s in enumerate(rows):
        h, m = divmod(s.get("total_minutes", 0), 60)
        name = s.get("name") or s.get("username") or f"مستخدم #{str(s['user_id'])[-4:]}"
        streak = get_streak(s["user_id"])
        streak_str = f" 🔥{streak}" if streak > 1 else ""
        lines.append(f"{medals[i]} *{name}* — {h}س {m}د{streak_str}")

    await update.message.reply_text(
        f"🏆 *ترتيب المذاكرة — أفضل {len(lines)}*\n\n" + "\n".join(lines) + "\n\n_محدّث في الوقت الفعلي_",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Message guard ──────────────────────────────────────────────────────────
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type == "private":
        return
    if not update.effective_user:
        return

    chat_id = str(update.effective_chat.id)
    uid = str(update.effective_user.id)
    session = load_session(chat_id)

    if not session or session["state"] != "active":
        return
    if uid not in session["participants"]:
        return
    if uid in session.get("breaks", {}):
        return

    try:
        await update.message.delete()
        logger.info(f"Deleted message from {uid} in {chat_id}")
    except TelegramError as e:
        logger.warning(f"فشل حذف الرسالة من {uid}: {e} — تأكد إن البوت أدمن وعنده صلاحية حذف الرسايل")
        return

    try:
        ref = mention(update.effective_user.first_name, uid)
        msg = random.choice(GUARD_MESSAGES).format(mention=ref)
        await context.bot.send_message(update.effective_chat.id, msg, parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        logger.warning(f"فشل إرسال رسالة التحذير: {e}")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("❌ BOT_TOKEN مش متحدد.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("study", cmd_study))
    app.add_handler(CommandHandler("pomodoro", cmd_pomodoro))
    app.add_handler(CommandHandler("break", cmd_break))
    app.add_handler(CommandHandler("back", cmd_back))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CallbackQueryHandler(join_callback, pattern=r"^join_"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, guard_messages))

    logger.info("✅ StudyLock Bot شغّال...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
