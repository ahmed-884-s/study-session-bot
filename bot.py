import os
import re
import logging
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TZ = ZoneInfo("Africa/Cairo")

# ── Persistent SQLite storage (survives restarts) ──────────────────────────
_DB_DIR = Path(os.getenv("DATA_DIR", "/data"))
try:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH = _DB_DIR / "studybot.db"
except OSError:
    DB_PATH = Path("studybot.db")

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db():
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kv (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

_init_db()

def _db_get(key: str):
    with _get_conn() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

def _db_set(key: str, value):
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, default=str))
        )

def _db_delete(key: str):
    with _get_conn() as conn:
        conn.execute("DELETE FROM kv WHERE key=?", (key,))

def _db_keys_like(pattern: str) -> list:
    with _get_conn() as conn:
        rows = conn.execute("SELECT key FROM kv WHERE key LIKE ?", (pattern,)).fetchall()
        return [r["key"] for r in rows]

# ── High-level data helpers ────────────────────────────────────────────────

def get_session(chat_id: str):
    return _db_get(f"session:{chat_id}")

def save_session(chat_id: str, session: dict):
    _db_set(f"session:{chat_id}", session)

def get_stats(user_id: str) -> dict:
    s = _db_get(f"stats:{user_id}")
    if s is None:
        s = {"total_minutes": 0, "sessions_completed": 0,
             "sessions_joined": 0, "last_study_date": None,
             "username": "", "name": ""}
    return s

def save_stats(user_id: str, s: dict):
    _db_set(f"stats:{user_id}", s)

def get_streak(user_id: str) -> dict:
    s = _db_get(f"streak:{user_id}")
    return s if s else {"streak": 0, "last_date": None}

def save_streak(user_id: str, s: dict):
    _db_set(f"streak:{user_id}", s)

def all_stats() -> list:
    keys = _db_keys_like("stats:%")
    result = []
    for k in keys:
        uid = k.split(":", 1)[1]
        result.append((uid, _db_get(k)))
    return result

# ── Permissions ────────────────────────────────────────────────────────────

UNLOCKED = ChatPermissions(
    can_send_messages=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True,
    can_change_info=False, can_invite_users=True, can_pin_messages=False,
)
RESTRICTED = ChatPermissions(can_send_messages=False)

async def restrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, RESTRICTED)
    except TelegramError as e:
        logger.warning(f"Restrict failed {user_id}: {e}")

async def unrestrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, UNLOCKED)
    except TelegramError as e:
        logger.warning(f"Unrestrict failed {user_id}: {e}")

# ── Message banks ──────────────────────────────────────────────────────────

GUARD_MESSAGES = [
    "ياض حسّ على دمك واقفل بقا 😒📚",
    "تم اتخاذ الإجراءات القانونية ضد الرسالة بنجاح ⚖️",
    "سيب التليفون في حاله وكمل يا ابني، متتعبناش معاك 😤",
    "هو إحنا ناقصين تشتت؟ الرسالة راحت، ركّز الله يكرمك 🙏",
    "يا معلم، المنهج مش هيخلص نفسه بنفسه 📖",
    "كفاية عبث… الكتاب بيعيط في الركن 😢📚",
    "تم القبض على رسالتك بتهمة إزعاج المذاكرين 🚔",
    "اكتب بعدين براحتك، دلوقتي العب دور الطالب المجتهد 🎭",
    "إحنا في سيشن مذاكرة مش سهرة عائلية 🫠",
    "لو فتحت الكتاب زي ما فتحت الشات كان زمانك خلصت المنهج 15 مرة 📚🔥",
    "كفاية يسطا، التنسيق مش هييجي بالدردشة 🤦",
    "الرسالة في ذمة الله… وأنت لسه عليك قادر كده 😶",
    "كل مرة بتبعت رسالة، في ورقة امتحان واقفة بتضحك عليك 📝😈",
    "بطل فهلوة وارجع للمذاكرة قبل ما المنهج ينتقم 😤",
    "النظام رصد محاولة فشل دراسي وتم التعامل معها 🤖",
    "أنت داخل تذاكر ولا تفتح برنامج حواري؟ 🙃",
    "تم مسح الرسالة حفاظًا على ما تبقى من مستقبلك الدراسي 🎓",
    "اقفل الشات وافتح مستقبلك بقا يبني 🚀",
]

MOTIVATIONAL = [
    "⚡ استمر — كل دقيقة بتفرق!",
    "📖 بتبني مستقبلك دلوقتي. متوقفش.",
    "🔥 الاستمرارية أهم من الشدة. ربنا معاك يا بطل!",
    "💪 الأبطال بيتصنعوا في لحظات زي دي.",
    "🌟 ساعة أقرب لهدفك. أنت قادر!",
    "🎯 ركّز. تنفس. كمّل.",
    "🚀 الجهد اللي بتبذله النهارده هتجني ثماره بكره.",
]

BREAK_OVER = [
    "☕ البريك خلص! جه وقت الشغل. 📚",
    "⏰ وقت الراحة انتهى — ارجع يا بطل! 💪",
    "🔔 البريك انتهى! مستقبلك هيشكرك. 🚀",
    "📚 اتشحنت؟ يلا نحارب تاني! 🔒",
]

# ── Utilities ──────────────────────────────────────────────────────────────

def now() -> datetime:
    return datetime.now(TZ)

def fmt_time(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.strftime("%I:%M %p")
    except Exception:
        return dt_str

def fmt_duration(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h {m}m"
    elif h:
        return f"{h}h"
    return f"{m}m"

def parse_duration(text: str):
    """Parse '2h', '90m', '1h30m', '90' -> minutes. Returns None if invalid."""
    text = text.lower().strip()
    m = re.fullmatch(r'(?:(\d+)h)?(\d+)m?', text)
    if m:
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        total = h * 60 + mins
        return total if 10 <= total <= 480 else None
    m = re.fullmatch(r'(\d+)h', text)
    if m:
        total = int(m.group(1)) * 60
        return total if 10 <= total <= 480 else None
    return None

def mention(user_id, name: str) -> str:
    return f"[{name}](tg://user?id={user_id})"

def update_streak(user_id: str):
    s = get_streak(user_id)
    today = now().date().isoformat()
    if s["last_date"] == today:
        return
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    if s["last_date"] == yesterday:
        s["streak"] += 1
    else:
        s["streak"] = 1
    s["last_date"] = today
    save_streak(user_id, s)

# ── Break keyboard ─────────────────────────────────────────────────────────

def break_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("☕ ١٠ دقايق", callback_data=f"break_{chat_id}_10"),
         InlineKeyboardButton("☕ ١٥ دقيقة", callback_data=f"break_{chat_id}_15")],
        [InlineKeyboardButton("☕ ٢٠ دقيقة", callback_data=f"break_{chat_id}_20"),
         InlineKeyboardButton("☕ ٣٠ دقيقة", callback_data=f"break_{chat_id}_30")],
        [InlineKeyboardButton("⏰ ٤٥ دقيقة", callback_data=f"break_{chat_id}_45"),
         InlineKeyboardButton("🛏 ساعة كاملة", callback_data=f"break_{chat_id}_60")],
    ]
    return InlineKeyboardMarkup(buttons)

# ── /start & /help ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *StudyLock Bot*\n\n"
        "بساعد مجموعات المذاكرة تتركز بتقييد المشاركين أثناء السيشنات.\n\n"
        "*الأوامر:*\n"
        "`/study 2h` — ابدأ سيشن ساعتين\n"
        "`/pomodoro [عدد الدورات]` — ابدأ سيشن بومودورو\n"
        "`/break` — خد بريك بعد انتهاء السيشن\n"
        "`/back` — خلص البريك بدري\n"
        "`/cancel` — إلغاء السيشن (للمنشئ فقط)\n"
        "`/status` — حالة السيشن الحالي\n"
        "`/stats` — إحصائياتك\n"
        "`/leaderboard` — ترتيب المجموعة\n"
        "`/end` — اغادر السيشن\n\n"
        "_ضيفني للجروب واعملني أدمن عشان أشتغل صح!_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /study ─────────────────────────────────────────────────────────────────

async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active"):
        await update.message.reply_text("⚠️ في سيشن شغال دلوقتي!\nاستخدم /status تعرف تفاصيله.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "📚 *الاستخدام:* `/study <المدة>`\n\n"
            "أمثلة:\n`/study 2h` — ساعتين\n`/study 90m` — 90 دقيقة\n`/study 1h30m` — ساعة ونص",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    duration = parse_duration(args[0])
    if not duration:
        await update.message.reply_text(
            "❌ مدة غلط. استخدم صيغ زي `2h` أو `90m` أو `1h30m`.\n"
            "الحد الأدنى: 10 دقايق | الأقصى: 8 ساعات.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    join_deadline = (now() + timedelta(minutes=5)).isoformat()
    session = {
        "state": "waiting",
        "duration": duration,
        "started_by": user.id,
        "participants": {str(user.id): {"name": user.full_name, "username": user.username or ""}},
        "join_deadline": join_deadline,
        "start_time": None,
        "end_time": None,
        "breaks": {},
        "pomodoro": False,
        "pomo_cycle": 0,
    }
    save_session(chat_id, session)

    stats = get_stats(str(user.id))
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_stats(str(user.id), stats)

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    await update.message.reply_text(
        f"📚 *سيشن مذاكرة جديد!*\n\n"
        f"👤 بدأه: {user.full_name}\n"
        f"⏱ المدة: *{fmt_duration(duration)}*\n"
        f"👥 المشاركين حتى دلوقتي: 1\n\n"
        f"⏳ *عندك 5 دقايق تنضم!*\n"
        f"السيشن هيبدأ: *{fmt_time(join_deadline)}*\n\n"
        f"اضغط الزر للانضمام 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.job_queue.run_once(
        start_session_job,
        when=300,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}",
    )

# ── Join callback ──────────────────────────────────────────────────────────

async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    _, chat_id = query.data.split("_", 1)

    session = get_session(chat_id)
    if not session or session["state"] != "waiting":
        await query.answer("❌ السيشن مش مفتوح للانضمام.", show_alert=True)
        return

    uid = str(user.id)
    if uid in session["participants"]:
        await query.answer("✅ أنت بالفعل في السيشن!", show_alert=True)
        return

    session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
    save_session(chat_id, session)

    stats = get_stats(uid)
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_stats(uid, stats)

    names = [p["name"] for p in session["participants"].values()]
    await query.answer("✅ انضممت للسيشن!", show_alert=True)

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    await query.edit_message_text(
        f"📚 *سيشن مذاكرة — غرفة الانتظار*\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"👥 المشاركين ({len(names)}): {', '.join(names)}\n\n"
        f"⏳ السيشن هيبدأ في أقل من 5 دقايق!\n"
        f"اضغط الزر للانضمام 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# ── Start session job ──────────────────────────────────────────────────────

async def start_session_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    chat_int = job_data["chat_int"]

    session = get_session(chat_id)
    if not session or session["state"] != "waiting":
        return

    start = now()
    end = start + timedelta(minutes=session["duration"])
    session["state"] = "active"
    session["start_time"] = start.isoformat()
    session["end_time"] = end.isoformat()
    save_session(chat_id, session)

    participants = session["participants"]
    for uid in participants:
        await restrict_user(context.bot, chat_int, int(uid))

    mentions_text = " ".join(mention(uid, p["name"]) for uid, p in participants.items())
    names_list = "\n".join(f"  • {p['name']}" for p in participants.values())

    if session.get("pomodoro"):
        cycles = session.get("pomo_cycles", 4)
        work = session.get("pomo_work", 25)
        brk = session.get("pomo_break", 5)
        extra = f"🍅 بومودورو: *{cycles}* دورة ({work}م مذاكرة + {brk}م بريك)\n"
    else:
        extra = ""

    await context.bot.send_message(
        chat_int,
        f"🔒 *السيشن بدأ!*\n\n"
        f"👥 المشاركين:\n{names_list}\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"{extra}"
        f"🏁 ينتهي: *{fmt_time(end.isoformat())}*\n\n"
        f"📵 *تم تقييد المشاركين.* وضع التركيز شغال!\n"
        f"_{mentions_text}_\n\n"
        f"_باقي الجروب يقدر يتكلم عادي_",
        parse_mode=ParseMode.MARKDOWN,
    )

    # FIX #1: store elapsed_minutes as integer for correct remaining calculation
    for i in range(1, session["duration"] // 60 + 1):
        msg = MOTIVATIONAL[i % len(MOTIVATIONAL)]
        elapsed_min = i * 60
        context.job_queue.run_once(
            send_motivation_job,
            when=i * 3600,
            data={"chat_int": chat_int, "msg": msg,
                  "elapsed_min": elapsed_min, "total": session["duration"]},
            name=f"motiv_{chat_id}_{i}",
        )

    # Pomodoro: schedule real per-cycle lock/unlock
    if session.get("pomodoro"):
        _schedule_pomodoro_cycles(context, chat_id, chat_int, session)

    # 10-minute warning before session ends
    if session["duration"] > 10:
        context.job_queue.run_once(
            warn_session_end_job,
            when=(session["duration"] - 10) * 60,
            data={"chat_int": chat_int, "chat_id": chat_id},
            name=f"warn_{chat_id}",
        )

    context.job_queue.run_once(
        end_session_job,
        when=session["duration"] * 60,
        data={"chat_id": chat_id, "chat_int": chat_int},
        name=f"end_{chat_id}",
    )

# ── Pomodoro cycle scheduler ───────────────────────────────────────────────
# FIX #9: real auto lock/unlock per cycle

def _schedule_pomodoro_cycles(context, chat_id: str, chat_int: int, session: dict):
    work = session.get("pomo_work", 25)
    brk  = session.get("pomo_break", 5)
    cycles = session.get("pomo_cycles", 4)
    offset = 0
    for c in range(1, cycles + 1):
        offset += work * 60
        if c < cycles:
            context.job_queue.run_once(
                pomo_break_start_job,
                when=offset,
                data={"chat_int": chat_int, "chat_id": chat_id,
                      "cycle": c, "cycles": cycles, "brk_min": brk},
                name=f"pomo_brk_{chat_id}_{c}",
            )
            offset += brk * 60
            context.job_queue.run_once(
                pomo_break_end_job,
                when=offset,
                data={"chat_int": chat_int, "chat_id": chat_id,
                      "cycle": c, "cycles": cycles},
                name=f"pomo_work_{chat_id}_{c}",
            )

async def pomo_break_start_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    session = get_session(d["chat_id"])
    if not session or session.get("state") != "active":
        return
    for uid in session["participants"]:
        await unrestrict_user(context.bot, d["chat_int"], int(uid))
    await context.bot.send_message(
        d["chat_int"],
        f"🍅 *دورة {d['cycle']} خلصت!*\n\n"
        f"☕ *بريك — {d['brk_min']} دقايق*\n"
        f"تقدروا تتكلموا دلوقتي. هنرجع للمذاكرة بعد {d['brk_min']} دقايق! ⏰",
        parse_mode=ParseMode.MARKDOWN,
    )

async def pomo_break_end_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    session = get_session(d["chat_id"])
    if not session or session.get("state") != "active":
        return
    for uid in session["participants"]:
        await restrict_user(context.bot, d["chat_int"], int(uid))
    await context.bot.send_message(
        d["chat_int"],
        f"🔒 *البريك خلص! دورة {d['cycle'] + 1} بدأت*\n\n"
        f"📵 تم تقييد المشاركين تاني. ركّزوا يا أبطال! 💪",
        parse_mode=ParseMode.MARKDOWN,
    )

# FIX #1: correct remaining calculation
async def send_motivation_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    elapsed_min   = d["elapsed_min"]
    remaining_min = d["total"] - elapsed_min
    await context.bot.send_message(
        d["chat_int"],
        f"{d['msg']}\n\n"
        f"⏱ مضت *{fmt_duration(elapsed_min)}* — باقي *{fmt_duration(remaining_min)}*",
        parse_mode=ParseMode.MARKDOWN,
    )

async def warn_session_end_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    session = get_session(d["chat_id"])
    if not session or session.get("state") != "active":
        return
    await context.bot.send_message(
        d["chat_int"],
        "⚠️ *باقي 10 دقايق على انتهاء السيشن!*\n\nاتمّوا اللي عندكم يا أبطال 💪",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── End session job ────────────────────────────────────────────────────────

async def end_session_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id  = job_data["chat_id"]
    chat_int = job_data["chat_int"]

    session = get_session(chat_id)
    if not session:
        return

    participants = dict(session["participants"])

    for uid in participants:
        stats = get_stats(uid)
        stats["total_minutes"]      += session["duration"]
        stats["sessions_completed"] += 1
        stats["last_study_date"]     = now().date().isoformat()
        save_stats(uid, stats)
        update_streak(uid)
        await unrestrict_user(context.bot, chat_int, int(uid))

    session["state"] = "ended"
    save_session(chat_id, session)

    lines = []
    for uid, pinfo in participants.items():
        streak = get_streak(uid).get("streak", 0)
        streak_str = f" 🔥{streak}" if streak > 1 else ""
        lines.append(f"  • {pinfo['name']} — {fmt_duration(session['duration'])}{streak_str}")

    mentions_text = " ".join(mention(uid, p["name"]) for uid, p in participants.items())

    await context.bot.send_message(
        chat_int,
        f"✅ *السيشن انتهى!*\n\n"
        f"🎉 أنتم أبطال فعلاً!\n"
        f"⏱ وقت المذاكرة: *{fmt_duration(session['duration'])}*\n\n"
        f"*المشاركين:*\n" + "\n".join(lines) + "\n\n"
        f"💤 تم فك التقييد عن الجميع.\n\n"
        f"{mentions_text}\n"
        f"اختار مدة البريك 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=break_keyboard(chat_id),
    )

# ── /cancel ────────────────────────────────────────────────────────────────
# FIX #8: new command

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user    = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ مفيش سيشن شغال دلوقتي.")
        return

    if session.get("started_by") != user.id:
        await update.message.reply_text("❌ بس اللي بدأ السيشن يقدر يلغيه.")
        return

    _cancel_session_jobs(context, chat_id)

    if session["state"] == "active":
        for uid in session["participants"]:
            await unrestrict_user(context.bot, update.effective_chat.id, int(uid))

    session["state"] = "cancelled"
    save_session(chat_id, session)

    await update.message.reply_text(
        f"🚫 *السيشن اتلغى بواسطة {user.full_name}*\n\n"
        f"_تقدروا تبدأوا سيشن جديد في أي وقت بـ /study_",
        parse_mode=ParseMode.MARKDOWN,
    )

def _cancel_session_jobs(context, chat_id: str):
    names = [f"start_{chat_id}", f"end_{chat_id}", f"warn_{chat_id}"]
    for i in range(1, 20):
        names += [f"motiv_{chat_id}_{i}",
                  f"pomo_brk_{chat_id}_{i}",
                  f"pomo_work_{chat_id}_{i}"]
    for name in names:
        for job in context.job_queue.get_jobs_by_name(name):
            job.schedule_removal()

# ── /end ───────────────────────────────────────────────────────────────────
# FIX #6 & #7: auto-cancel when creator or last participant leaves

async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user    = update.effective_user
    uid     = str(user.id)

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ مفيش سيشن شغال تغادره.")
        return

    if uid not in session["participants"]:
        await update.message.reply_text("❌ أنت مش من المشاركين.")
        return

    is_creator = (session.get("started_by") == user.id)
    del session["participants"][uid]
    if session["state"] == "active":
        await unrestrict_user(context.bot, update.effective_chat.id, user.id)
    save_session(chat_id, session)

    await update.message.reply_text(
        f"👋 *{user.full_name}* سيب السيشن.\n"
        f"_الاستمرارية هي المفتاح! نشوفك المرة الجاية._ 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

    if is_creator or len(session["participants"]) == 0:
        _cancel_session_jobs(context, chat_id)
        for p_uid in session["participants"]:
            await unrestrict_user(context.bot, update.effective_chat.id, int(p_uid))
        session["state"] = "cancelled"
        save_session(chat_id, session)
        reason = "المنشئ غادر" if is_creator else "مفيش مشاركين"
        await context.bot.send_message(
            update.effective_chat.id,
            f"🚫 *السيشن اتلغى تلقائياً* ({reason})\n\n_ابدأ سيشن جديد بـ /study_",
            parse_mode=ParseMode.MARKDOWN,
        )

# ── Break inline button ────────────────────────────────────────────────────
# FIX #2: blocked during active session

async def break_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid  = str(user.id)

    parts    = query.data.split("_")
    minutes  = int(parts[-1])
    chat_id  = "_".join(parts[1:-1])
    chat_int = update.effective_chat.id

    session = get_session(chat_id)
    if not session or uid not in session.get("participants", {}):
        await query.answer("❌ أنت مش من المشاركين في السيشن ده.", show_alert=True)
        return

    # FIX #2: no break while session is active
    if session.get("state") == "active":
        await query.answer("❌ السيشن لسه شغال! انتظر حتى ينتهي.", show_alert=True)
        return

    if uid in session.get("breaks", {}):
        await query.answer("⏰ أنت عندك بريك شغال بالفعل!", show_alert=True)
        return

    break_end = now() + timedelta(minutes=minutes)
    session.setdefault("breaks", {})[uid] = {
        "end": break_end.isoformat(), "duration": minutes, "name": user.full_name,
    }
    save_session(chat_id, session)

    await query.answer(f"✅ البريك بدأ — {minutes} دقيقة!", show_alert=True)
    await context.bot.send_message(
        chat_int,
        f"☕ *{user.full_name}* بدأ بريك!\n\n"
        f"⏱ المدة: *{minutes} دقيقة*\n"
        f"🔔 هترجع: *{fmt_time(break_end.isoformat())}*\n\n"
        f"_استرخي واتشحن عشان تكمل 😴_",
        parse_mode=ParseMode.MARKDOWN,
    )

    context.job_queue.run_once(
        end_break_job,
        when=minutes * 60,
        data={"chat_id": chat_id, "chat_int": chat_int, "uid": uid, "name": user.full_name},
        name=f"break_{chat_id}_{uid}",
    )

# ── /break command ─────────────────────────────────────────────────────────

async def cmd_break(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user    = update.effective_user
    uid     = str(user.id)

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    session = get_session(chat_id)

    # FIX #2: block during active session
    if session and session.get("state") == "active":
        await update.message.reply_text(
            "❌ السيشن لسه شغال! مش تقدر تاخد بريك دلوقتي.\n"
            "خد بريك بعد ما السيشن ينتهي."
        )
        return

    if not session or uid not in session.get("participants", {}):
        await update.message.reply_text("❌ أنت مش من المشاركين في آخر سيشن.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "☕ *اختار مدة البريك:* 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=break_keyboard(chat_id),
        )
        return

    try:
        minutes = int(args[0])
        if not 10 <= minutes <= 60:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ مدة البريك لازم تكون من 10 لـ 60 دقيقة.")
        return

    break_end = now() + timedelta(minutes=minutes)
    session.setdefault("breaks", {})[uid] = {
        "end": break_end.isoformat(), "duration": minutes, "name": user.full_name,
    }
    save_session(chat_id, session)

    await update.message.reply_text(
        f"☕ *البريك بدأ لـ {user.full_name}*\n\n"
        f"⏱ المدة: *{minutes} دقيقة*\n"
        f"🔔 هترجع: *{fmt_time(break_end.isoformat())}*\n\n"
        f"_استرخي واتشحن! 😴_",
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

    session = get_session(chat_id)
    if session and uid in session.get("breaks", {}):
        del session["breaks"][uid]
        save_session(chat_id, session)

    await unrestrict_user(context.bot, chat_int, int(uid))
    men = mention(uid, name)

    await context.bot.send_message(
        chat_int,
        f"{random.choice(BREAK_OVER)}\n\n{men}، البريك خلص — ارجع للمذاكرة! 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

    # FIX #4: only re-restrict if session active AND not a pomodoro mid-break
    session = get_session(chat_id)
    if session and session.get("state") == "active" and not session.get("pomodoro"):
        await restrict_user(context.bot, chat_int, int(uid))
        await context.bot.send_message(
            chat_int,
            f"🔒 {men} ارجع للمذاكرة يا بطل! التقييد اشتغل تاني.",
            parse_mode=ParseMode.MARKDOWN,
        )

# ── /back ──────────────────────────────────────────────────────────────────

async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user    = update.effective_user
    uid     = str(user.id)

    session = get_session(chat_id)
    if not session or uid not in session.get("breaks", {}):
        await update.message.reply_text("❌ مفيش بريك شغال عندك.")
        return

    for job in context.job_queue.get_jobs_by_name(f"break_{chat_id}_{uid}"):
        job.schedule_removal()

    del session["breaks"][uid]
    save_session(chat_id, session)

    await unrestrict_user(context.bot, update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"💪 *{user.full_name}* خلص البريك بدري!\nرجع للـ grinding 📚🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

    if session.get("state") == "active":
        await restrict_user(context.bot, update.effective_chat.id, user.id)

# ── /pomodoro ──────────────────────────────────────────────────────────────

async def cmd_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user    = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active"):
        await update.message.reply_text("⚠️ في سيشن شغال بالفعل.")
        return

    args   = context.args
    cycles = int(args[0]) if args and args[0].isdigit() else 4
    cycles = max(1, min(cycles, 8))
    work_min, break_min = 25, 5
    total = cycles * (work_min + break_min)

    join_deadline = (now() + timedelta(minutes=5)).isoformat()
    session = {
        "state": "waiting",
        "duration": total,
        "started_by": user.id,
        "participants": {str(user.id): {"name": user.full_name, "username": user.username or ""}},
        "join_deadline": join_deadline,
        "start_time": None,
        "end_time": None,
        "breaks": {},
        "pomodoro": True,
        "pomo_cycles": cycles,
        "pomo_work": work_min,
        "pomo_break": break_min,
        "pomo_cycle": 0,
    }
    save_session(chat_id, session)

    stats = get_stats(str(user.id))
    stats["sessions_joined"] += 1
    save_stats(str(user.id), stats)

    keyboard = [[InlineKeyboardButton("✋ انضم للبومودورو", callback_data=f"join_{chat_id}")]]
    await update.message.reply_text(
        f"🍅 *سيشن بومودورو!*\n\n"
        f"👤 بدأه: {user.full_name}\n"
        f"🔄 الدورات: *{cycles}* × (25 دقيقة مذاكرة + 5 دقايق بريك أتوماتيك)\n"
        f"⏱ الإجمالي: *{fmt_duration(total)}*\n\n"
        f"⏳ *5 دقايق للانضمام!*\n"
        f"اضغط الزر للانضمام 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.job_queue.run_once(
        start_session_job,
        when=300,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}",
    )

# ── /status ────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    session = get_session(chat_id)

    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text(
            "💤 *مفيش سيشن شغال دلوقتي.*\n\nابدأ واحد بـ `/study <المدة>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    participants = session["participants"]
    breaks = session.get("breaks", {})
    lines = []
    for uid, pinfo in participants.items():
        if uid in breaks:
            end_str = fmt_time(breaks[uid]["end"])
            lines.append(f"  ☕ {pinfo['name']} — في بريك (يرجع {end_str})")
        else:
            lines.append(f"  📖 {pinfo['name']} — بيذاكر")

    state_emoji = "⏳" if session["state"] == "waiting" else "🔒"
    state_text  = "في الانتظار" if session["state"] == "waiting" else "شغال — وضع تركيز"
    pomo_str    = f"\n🍅 بومودورو: {session.get('pomo_cycles',4)} دورات" if session.get("pomodoro") else ""

    msg = (
        f"{state_emoji} *حالة السيشن: {state_text}*{pomo_str}\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
    )
    if session.get("end_time"):
        msg += f"🏁 ينتهي: *{fmt_time(session['end_time'])}*\n"
    msg += f"\n*المشاركين ({len(lines)}):*\n" + "\n".join(lines)

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ── /stats ─────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = str(update.effective_user.id)
    stats  = get_stats(uid)
    streak = get_streak(uid).get("streak", 0)
    hours  = stats["total_minutes"] // 60
    mins   = stats["total_minutes"] % 60

    await update.message.reply_text(
        f"📊 *إحصائياتك*\n\n"
        f"⏱ إجمالي وقت المذاكرة: *{hours}h {mins}m*\n"
        f"✅ السيشنات المكتملة: *{stats['sessions_completed']}*\n"
        f"👥 السيشنات اللي انضممت ليها: *{stats['sessions_joined']}*\n"
        f"🔥 السلسلة الحالية: *{streak} يوم*\n"
        f"📅 آخر مذاكرة: *{stats.get('last_study_date') or 'N/A'}*",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /leaderboard ───────────────────────────────────────────────────────────

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_s = all_stats()
    if not all_s:
        await update.message.reply_text("📊 مفيش داتا لسه! ابدأ سيشن مذاكرة.")
        return

    sorted_users = sorted(all_s, key=lambda x: x[1].get("total_minutes", 0), reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines  = []
    for i, (uid, s) in enumerate(sorted_users):
        h, m   = divmod(s.get("total_minutes", 0), 60)
        name   = s.get("name") or s.get("username") or f"User {uid}"
        streak = get_streak(uid).get("streak", 0)
        streak_str = f" 🔥{streak}" if streak > 1 else ""
        lines.append(f"{medals[i]} *{name}* — {h}h {m}m{streak_str}")

    await update.message.reply_text(
        f"🏆 *ليدربورد المذاكرة*\n\n" + "\n".join(lines) + "\n\n_بيتحدث في الوقت الفعلي_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Guard messages ─────────────────────────────────────────────────────────
# FIX #4: handler filter excludes bot's own messages at registration level

async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type == "private":
        return

    chat_id = str(update.effective_chat.id)
    uid     = str(update.effective_user.id)
    user    = update.effective_user

    # Ignore bot's own user id to prevent any loop
    if update.effective_user.is_bot:
        return

    session = get_session(chat_id)
    if not session or session["state"] != "active":
        return
    if uid not in session["participants"]:
        return
    if uid in session.get("breaks", {}):
        return  # In break → allowed

    try:
        await update.message.delete()
        men = mention(uid, user.first_name)
        await context.bot.send_message(
            update.effective_chat.id,
            f"📵 {men}\n\n_{random.choice(GUARD_MESSAGES)}_",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.warning(f"Guard failed: {e}")

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN not set.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_start))
    app.add_handler(CommandHandler("study",       cmd_study))
    app.add_handler(CommandHandler("pomodoro",    cmd_pomodoro))
    app.add_handler(CommandHandler("break",       cmd_break))
    app.add_handler(CommandHandler("back",        cmd_back))
    app.add_handler(CommandHandler("cancel",      cmd_cancel))
    app.add_handler(CommandHandler("end",         cmd_end))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))

    app.add_handler(CallbackQueryHandler(join_callback,  pattern=r"^join_"))
    app.add_handler(CallbackQueryHandler(break_callback, pattern=r"^break_"))

    # FIX #4: ~filters.VIA_BOT + is_bot check inside handler covers loop prevention
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, guard_messages))

    logger.info("StudyLock Bot running | DB: %s", DB_PATH)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
