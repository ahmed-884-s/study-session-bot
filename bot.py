import os
import asyncio
import logging
import json
import random
import re
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
DATA_FILE = Path("/tmp/studybot_data.json")
TZ = ZoneInfo("Africa/Cairo")

# ── Permissions ────────────────────────────────────────────────────────────

UNLOCKED = ChatPermissions(
    can_send_messages=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True,
    can_change_info=False, can_invite_users=True, can_pin_messages=False,
)

# ── Data persistence ───────────────────────────────────────────────────────

def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {"sessions": {}, "stats": {}, "streaks": {}, "goals": {}, "reminders": {}, "admins": {}}

def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, default=str, ensure_ascii=False))

data = load_data()

# تأكيد وجود الـ keys الجديدة في الداتا القديمة
data.setdefault("goals", {})
data.setdefault("reminders", {})
data.setdefault("admins", {})

_announced_chats: set = set()

# ── Messages ───────────────────────────────────────────────────────────────

MOTIVATIONAL = [
    "⚡ استمر — كل دقيقة بتفرق!",
    "📖 بتتبني مستقبلك دلوقتي. ماتوقفش.",
    "🔥 الاستمرارية أهم من الشدة. ركز!",
    "💪 الأبطال بيتصنعوا في لحظات زي دي.",
    "🌟 بقيت أقرب لهدفك بساعة. تقدر!",
    "🎯 ركز. اتنفس. كمل.",
    "🚀 المجهود اللي بتبذله النهارده هتجني ثماره بكرة.",
    "📚 كل صفحة بتقلبها دي خطوة لأحلامك.",
    "💡 العبقرية مش موهبة — هي تكرار ومجهود.",
]

BREAK_OVER = [
    "☕ الراحة خلصت! وقت الشغل. 📚",
    "⏰ وقت الراحة انتهى — ارجع يا بطل! 💪",
    "🔔 الاستراحة خلصت! ارجع حارب. 🚀",
    "🎯جسمك اتشحن — دماغك جاهزة! يلا نكمل!",
]

DELETE_MESSAGES = [
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
    "الرسالة في ذمة الله… وأنت لسه عليك باب كامل 😶",
    "كل مرة تبعت رسالة، في ورقة امتحان هناك بتضحك عليك 📝😈",
    "النظام رصد محاولة فشل دراسي وتم التعامل معها 🤖",
    "تم مسح الرسالة حفاظًا على ما تبقى من مستقبلك الدراسي 🎓",
    "اقفل الشات وافتح مستقبلك بقا يبني 🚀",
]

# رسايل التحفيز نص الوقت
HALFWAY_MESSAGES = [
    "⚡ *نص الطريق خلص!* كمل الجزء التاني وأنت أقوى!",
    "🔥 *نص الوقت خلص بجد!* الجزء التاني أسهل دايماً — استمر!",
    "💡 *نص الوقت مضى!* المكملين هينجحوا، والقاعدين هيتفرجوا!",
]

# ── Constants ──────────────────────────────────────────────────────────────
POINTS_PER_MINUTE = 1
POINTS_PENALTY = 5
MAX_BREAK_MINUTES = 60
MAX_GOAL_HOURS_DAILY = 24

# ── Utility ────────────────────────────────────────────────────────────────

def now() -> datetime:
    return datetime.now(TZ)

def fmt_time(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).strftime("%I:%M %p")
    except Exception:
        return dt_str

def fmt_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}س {m}د"
    elif h:
        return f"{h}س"
    return f"{m}د"

def parse_duration(text: str) -> int | None:
    text = text.lower().strip()
    match = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m?)?', text)
    if not match or not any(match.groups()):
        return None
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    total = h * 60 + m
    return total if 10 <= total <= 480 else None

def get_session(chat_id: str, session_id: str = None) -> dict | None:
    sessions = data["sessions"].get(chat_id, {})
    if not sessions:
        return None
    if session_id and session_id in sessions:
        return sessions[session_id]
    for s_id, s in sessions.items():
        if s.get("state") in ("waiting", "active"):
            return s
    return None

def get_active_sessions_count(chat_id: str) -> int:
    sessions = data["sessions"].get(chat_id, {})
    return sum(1 for s in sessions.values() if s.get("state") in ("waiting", "active"))

def get_stats(user_id: str) -> dict:
    return data["stats"].setdefault(user_id, {
        "total_minutes": 0, "sessions_completed": 0,
        "sessions_joined": 0, "last_study_date": None,
        "username": "", "name": "",
        "points": 0,
        "weekly_points": 0,
        "weekly_minutes": 0,
        "daily_minutes": 0,
        "badges": [],
    })

def update_streak(user_id: str):
    s = data["streaks"].setdefault(user_id, {"streak": 0, "last_date": None, "max_streak": 0})
    today = now().date().isoformat()
    if s["last_date"] == today:
        return
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    if s["last_date"] == yesterday:
        s["streak"] += 1
    else:
        s["streak"] = 1
    s["last_date"] = today
    # تحديث أعلى سلسلة
    if s["streak"] > s.get("max_streak", 0):
        s["max_streak"] = s["streak"]

def add_points(user_id: str, points: int):
    stats = get_stats(user_id)
    stats["points"] = max(0, stats.get("points", 0) + points)
    stats["weekly_points"] = max(0, stats.get("weekly_points", 0) + points)

def check_and_award_badges(user_id: str) -> list[str]:
    """فحص وإعطاء شارات جديدة للمستخدم"""
    stats = get_stats(user_id)
    streak = data["streaks"].get(user_id, {}).get("streak", 0)
    badges = stats.setdefault("badges", [])
    new_badges = []

    badge_rules = [
        ("🌱 مبتدئ", stats["sessions_completed"] >= 1),
        ("📚 مذاكر", stats["sessions_completed"] >= 5),
        ("🏃 مداوم", stats["sessions_completed"] >= 20),
        ("🏆 بطل", stats["sessions_completed"] >= 50),
        ("⏱ ساعة", stats["total_minutes"] >= 60),
        ("🕐 10 ساعات", stats["total_minutes"] >= 600),
        ("🕑 50 ساعة", stats["total_minutes"] >= 3000),
        ("🔥 3 أيام", streak >= 3),
        ("🔥🔥 أسبوع", streak >= 7),
        ("🔥🔥🔥 شهر", streak >= 30),
        ("⭐ 100 نقطة", stats["points"] >= 100),
        ("💎 1000 نقطة", stats["points"] >= 1000),
    ]

    for badge_name, condition in badge_rules:
        if condition and badge_name not in badges:
            badges.append(badge_name)
            new_badges.append(badge_name)

    return new_badges

def build_leaderboard_text(title: str, sort_key: str = "points") -> str:
    if not data["stats"]:
        return "📊 لسه مفيش بيانات. ابدأ سيشن مذاكرة."

    sorted_users = sorted(
        data["stats"].items(),
        key=lambda x: x[1].get(sort_key, 0),
        reverse=True
    )[:10]

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = []
    for i, (uid, s) in enumerate(sorted_users):
        pts = s.get(sort_key, 0)
        if pts == 0:
            continue
        name = s.get("name") or s.get("username") or f"مستخدم {uid}"
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        streak_str = f" 🔥{streak}" if streak > 1 else ""
        if sort_key == "weekly_points":
            h, m = divmod(s.get("weekly_minutes", 0), 60)
            time_str = f" ({h}س {m}د)"
        else:
            h, m = divmod(s.get("total_minutes", 0), 60)
            time_str = f" ({h}س {m}د)"
        lines.append(f"{medals[i]} *{name}* — {pts} نقطة{time_str}{streak_str}")

    if not lines:
        return f"🏆 *{title}*\n\nلسه مفيش بيانات كافية."

    return f"🏆 *{title}*\n\n" + "\n".join(lines) + "\n\n_بيتحدث تلقائياً_"

# ── Pin/Unpin ──────────────────────────────────────────────────────────────

async def pin_message(bot, chat_id: int, message_id: int):
    try:
        await bot.pin_chat_message(chat_id, message_id, disable_notification=True)
    except TelegramError as e:
        logger.warning(f"فشل التثبيت: {e}")

async def unpin_message(bot, chat_id: int, message_id: int):
    try:
        await bot.unpin_chat_message(chat_id, message_id)
    except TelegramError as e:
        logger.warning(f"فشل إلغاء التثبيت: {e}")

async def maybe_announce(bot, chat_id: int):
    if chat_id in _announced_chats:
        return
    _announced_chats.add(chat_id)
    try:
        await bot.send_message(
            chat_id,
            "📚 *StudyLock Bot جاهز!*\n\nاستخدم /start لمشاهدة كل الأوامر.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

# ── Admin check ────────────────────────────────────────────────────────────

async def is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

# ── /reset ─────────────────────────────────────────────────────────────────

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_id = update.effective_user.id

    # التحقق من الأدمن
    if not await is_admin(context.bot, update.effective_chat.id, user_id):
        await update.message.reply_text("❌ الأمر ده للأدمن بس.")
        return

    sessions = data["sessions"].get(chat_id, {})

    if not sessions:
        await update.message.reply_text("✅ مفيش سيشنات شغالة عشان تتنضف.")
        return

    chat_int = update.effective_chat.id
    for s_id, s in sessions.items():
        active_pin = s.get("active_pinned_message_id")
        if active_pin:
            await unpin_message(context.bot, chat_int, active_pin)
        pinned = s.get("pinned_message_id")
        if pinned and pinned != active_pin:
            await unpin_message(context.bot, chat_int, pinned)

        for job in context.job_queue.jobs():
            if job.name and (
                job.name.startswith(f"start_{chat_id}_{s_id}") or
                job.name.startswith(f"end_{chat_id}_{s_id}") or
                job.name.startswith(f"motiv_{chat_id}_{s_id}") or
                job.name.startswith(f"halfway_{chat_id}_{s_id}")
            ):
                job.schedule_removal()

    del data["sessions"][chat_id]
    save_data(data)

    await update.message.reply_text("🧹 تم تنظيف كل السيشنات الشغالة والتثبيتات. تقدر تبدأ من جديد.")

# ── /study ─────────────────────────────────────────────────────────────────

async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في المجموعات بس.")
        return

    await maybe_announce(context.bot, update.effective_chat.id)

    args = context.args
    if not args:
        await update.message.reply_text(
            "📚 *الاستخدام:* `/study <المدة>`\n\n"
            "أمثلة:\n`/study 1h` — ساعة\n`/study 90m` — 90 دقيقة\n`/study 1h30m` — ساعة ونص",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    duration = parse_duration(args[0])
    if not duration:
        await update.message.reply_text(
            "❌ مدة مش صحيحة. استخدم صيغ زي `1h`، `90m`، `1h30m`.\n"
            "الحد الأدنى: 10 دقائق | الحد الأقصى: 8 ساعات.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # موضوع اختياري
    topic = " ".join(args[1:]) if len(args) > 1 else None

    session_id = f"s{datetime.now(TZ).strftime('%H%M%S')}{random.randint(100, 999)}"
    sessions = data["sessions"].setdefault(chat_id, {})

    sessions[session_id] = {
        "state": "waiting",
        "duration": duration,
        "topic": topic,
        "started_by": user.id,
        "participants": {str(user.id): {"name": user.full_name, "username": user.username or ""}},
        "start_time": None,
        "end_time": None,
        "breaks": {},
        "pomodoro": False,
        "pomo_cycle": 0,
        "pinned_message_id": None,
        "active_pinned_message_id": None,
    }
    save_data(data)

    stats = get_stats(str(user.id))
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    topic_line = f"\n📝 الموضوع: *{topic}*" if topic else ""
    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}_{session_id}")]]
    msg = await update.message.reply_text(
        f"📚 *سيشن مذاكرة جديدة!*\n\n"
        f"👤 بدأها: {user.full_name}\n"
        f"⏱ المدة: *{fmt_duration(duration)}*"
        f"{topic_line}\n"
        f"👥 المشاركين: 1\n\n"
        f"🚀 *السيشن هتبدأ على طول!*\n"
        f"اضغط الزرار للانضمام 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    sessions[session_id]["pinned_message_id"] = msg.message_id
    save_data(data)
    await pin_message(context.bot, update.effective_chat.id, msg.message_id)

    context.job_queue.run_once(
        start_session_job,
        when=2,
        data={"chat_id": chat_id, "session_id": session_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}_{session_id}",
    )

# ── Join callback ──────────────────────────────────────────────────────────

async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    _, chat_id, session_id = query.data.split("_", 2)

    sessions = data["sessions"].get(chat_id, {})
    session = sessions.get(session_id)

    if not session or session["state"] not in ("waiting", "active"):
        await query.answer("❌ السيشن دي اتقفلت أو انتهت.", show_alert=True)
        return

    uid = str(user.id)
    if uid in session["participants"]:
        await query.answer("✅ انت مشارك أصلاً!", show_alert=True)
        return

    session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
    stats = get_stats(uid)
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    names = [p["name"] for p in session["participants"].values()]
    await query.answer("✅ انضممت للسيشن!", show_alert=True)

    state_text = "⏳ بتستنى" if session["state"] == "waiting" else "🔒 شغالة"
    topic_line = f"\n📝 الموضوع: *{session['topic']}*" if session.get("topic") else ""
    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}_{session_id}")]]
    try:
        await query.edit_message_text(
            f"📚 *سيشن المذاكرة — {state_text}*\n\n"
            f"⏱ المدة: *{fmt_duration(session['duration'])}*"
            f"{topic_line}\n"
            f"👥 المشاركين ({len(names)}): {', '.join(names)}\n\n"
            f"اضغط للانضمام 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except TelegramError:
        pass

# ── Start session job ──────────────────────────────────────────────────────

async def start_session_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    session_id = job_data["session_id"]
    chat_int = job_data["chat_int"]

    sessions = data["sessions"].get(chat_id, {})
    session = sessions.get(session_id)

    if not session or session["state"] != "waiting":
        return

    start = now()
    end = start + timedelta(minutes=session["duration"])
    session["state"] = "active"
    session["start_time"] = start.isoformat()
    session["end_time"] = end.isoformat()
    save_data(data)

    participants = session["participants"]
    names = [p["name"] for p in participants.values()]
    topic_line = f"\n📝 الموضوع: *{session['topic']}*" if session.get("topic") else ""

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}_{session_id}")]]

    old_pin = session.get("pinned_message_id")
    if old_pin:
        await unpin_message(context.bot, chat_int, old_pin)

    sent = await context.bot.send_message(
        chat_int,
        f"🔒 *السيشن بدأت!*\n\n"
        f"👥 المشاركين: {', '.join(names)}\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*"
        f"{topic_line}\n"
        f"🏁 تنتهي الساعة: *{fmt_time(end.isoformat())}*\n\n"
        f"📵 *الرسايل هتتمسح أثناء السيشن* — ركز! 🎯\n"
        f"ممكن تنضم لسه 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    await pin_message(context.bot, chat_int, sent.message_id)
    session["active_pinned_message_id"] = sent.message_id
    save_data(data)

    # رسايل تحفيز كل ساعة
    for i in range(1, session["duration"] // 60 + 1):
        msg = MOTIVATIONAL[i % len(MOTIVATIONAL)]
        context.job_queue.run_once(
            send_motivation_job,
            when=i * 3600,
            data={"chat_int": chat_int, "msg": msg, "elapsed": fmt_duration(i * 60)},
            name=f"motiv_{chat_id}_{session_id}_{i}",
        )

    # رسالة نص الوقت
    half_seconds = (session["duration"] * 60) // 2
    if half_seconds > 60:
        context.job_queue.run_once(
            send_halfway_job,
            when=half_seconds,
            data={"chat_int": chat_int, "duration": session["duration"]},
            name=f"halfway_{chat_id}_{session_id}",
        )

    # جدولة نهاية السيشن
    context.job_queue.run_once(
        end_session_job,
        when=session["duration"] * 60,
        data={"chat_id": chat_id, "session_id": session_id, "chat_int": chat_int},
        name=f"end_{chat_id}_{session_id}",
    )

# ── Halfway job ────────────────────────────────────────────────────────────

async def send_halfway_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    msg = random.choice(HALFWAY_MESSAGES)
    await context.bot.send_message(
        d["chat_int"],
        f"{msg}\n\n⏱ نص وقت المذاكرة مضى — *{fmt_duration(d['duration'] // 2)}* من أصل *{fmt_duration(d['duration'])}*",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Motivation job ─────────────────────────────────────────────────────────

async def send_motivation_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await context.bot.send_message(
        d["chat_int"],
        f"{d['msg']}\n\n⏱ *{d['elapsed']}* مضوا",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── End session job ────────────────────────────────────────────────────────

async def end_session_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    session_id = job_data["session_id"]
    chat_int = job_data["chat_int"]

    sessions = data["sessions"].get(chat_id, {})
    session = sessions.get(session_id)
    if not session:
        return

    new_badges_all = []
    for uid, pdata_u in session["participants"].items():
        stats = get_stats(uid)
        stats["total_minutes"] += session["duration"]
        stats["weekly_minutes"] = stats.get("weekly_minutes", 0) + session["duration"]
        stats["daily_minutes"] = stats.get("daily_minutes", 0) + session["duration"]
        stats["sessions_completed"] += 1
        stats["last_study_date"] = now().date().isoformat()
        earned = session["duration"] * POINTS_PER_MINUTE
        add_points(uid, earned)
        update_streak(uid)
        new_badges = check_and_award_badges(uid)
        if new_badges:
            new_badges_all.append((pdata_u["name"], new_badges))

    session["state"] = "ended"
    save_data(data)

    active_pin = session.get("active_pinned_message_id")
    if active_pin:
        await unpin_message(context.bot, chat_int, active_pin)

    lines = []
    sorted_parts = sorted(
        session["participants"].items(),
        key=lambda x: get_stats(x[0]).get("points", 0),
        reverse=True
    )
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 20

    for i, (uid, pinfo) in enumerate(sorted_parts):
        stats = get_stats(uid)
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        streak_str = f" 🔥{streak}" if streak > 1 else ""
        earned = session["duration"] * POINTS_PER_MINUTE
        medal = medals[i] if i < len(medals) else "🏅"
        lines.append(f"{medal} {pinfo['name']} — +{earned} نقطة | المجموع: {stats.get('points', 0)}{streak_str}")

    badge_text = ""
    if new_badges_all:
        badge_lines = []
        for name, badges in new_badges_all:
            badge_lines.append(f"🎖 *{name}* فتح: {' '.join(badges)}")
        badge_text = "\n\n🎉 *شارات جديدة!*\n" + "\n".join(badge_lines)

    topic_line = f"\n📝 الموضوع: *{session['topic']}*" if session.get("topic") else ""

    await context.bot.send_message(
        chat_int,
        f"✅ *السيشن انتهت!*\n\n"
        f"🎉 عظيم — كلكم عملتوا حاجة كويسة النهارده!"
        f"{topic_line}\n"
        f"⏱ وقت المذاكرة: *{fmt_duration(session['duration'])}*\n\n"
        f"*🏆 الترتيب:*\n" + "\n".join(lines) +
        badge_text + "\n\n"
        f"💤 استريح — استخدم `/break <دقايق>` لتايمر استراحة.\n"
        f"📊 /stats لإحصائياتك | /leaderboard للترتيب الكامل",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Empty session check ────────────────────────────────────────────────────

async def check_and_end_empty_session(context, chat_id: str, session_id: str):
    sessions = data["sessions"].get(chat_id, {})
    session = sessions.get(session_id)

    if session and len(session.get("participants", {})) == 0:
        for job in context.job_queue.jobs():
            if job.name and (
                job.name.startswith(f"end_{chat_id}_{session_id}") or
                job.name.startswith(f"motiv_{chat_id}_{session_id}") or
                job.name.startswith(f"halfway_{chat_id}_{session_id}")
            ):
                job.schedule_removal()

        active_pin = session.get("active_pinned_message_id")
        if active_pin:
            await unpin_message(context.bot, int(chat_id), active_pin)

        del sessions[session_id]
        save_data(data)

# ── /break ─────────────────────────────────────────────────────────────────

async def cmd_break(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في المجموعات بس.")
        return

    sessions = data["sessions"].get(chat_id, {})
    target_session = None
    target_sid = None
    for s_id, s in sessions.items():
        if s.get("state") in ("ended", "active") and uid in s.get("participants", {}):
            target_session = s
            target_sid = s_id
            break

    if not target_session:
        await update.message.reply_text("❌ مفيش سيشن. ابدأ واحدة بـ /study.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "☕ *الاستخدام:* `/break <دقايق>`\n\nمثال: `/break 10`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        minutes = int(args[0])
        if not 1 <= minutes <= MAX_BREAK_MINUTES:
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"❌ المدة لازم تكون بين 1 و {MAX_BREAK_MINUTES} دقيقة.")
        return

    break_end = now() + timedelta(minutes=minutes)
    target_session["breaks"][uid] = {
        "end": break_end.isoformat(),
        "duration": minutes,
        "name": user.full_name,
    }
    save_data(data)

    await update.message.reply_text(
        f"☕ *استراحة لـ {user.full_name}*\n\n"
        f"⏱ المدة: *{minutes} دقيقة*\n"
        f"🔔 هترجع الساعة: *{fmt_time(break_end.isoformat())}*\n\n"
        f"_استرح — رسايلك مش هتتمسح دلوقتي!_ 😴",
        parse_mode=ParseMode.MARKDOWN,
    )

    context.job_queue.run_once(
        end_break_job,
        when=minutes * 60,
        data={"chat_id": chat_id, "session_id": target_sid, "chat_int": update.effective_chat.id, "uid": uid, "name": user.full_name},
        name=f"break_{chat_id}_{target_sid}_{uid}",
    )

# ── End break job ──────────────────────────────────────────────────────────

async def end_break_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    chat_id, session_id, chat_int, uid, name = d["chat_id"], d["session_id"], d["chat_int"], d["uid"], d["name"]

    sessions = data["sessions"].get(chat_id, {})
    session = sessions.get(session_id, {})
    if session and uid in session.get("breaks", {}):
        del session["breaks"][uid]
        save_data(data)

    msg = random.choice(BREAK_OVER)
    mention = f"[{name}](tg://user?id={uid})"
    await context.bot.send_message(
        chat_int,
        f"{msg}\n\n{mention}, استراحتك خلصت — ارجع تذاكر! 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /back ──────────────────────────────────────────────────────────────────

async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    sessions = data["sessions"].get(chat_id, {})
    target_sid = None
    for s_id, s in sessions.items():
        if uid in s.get("breaks", {}):
            target_sid = s_id
            break

    if not target_sid:
        await update.message.reply_text("❌ مفيش استراحة شغالة ليك.")
        return

    jobs = context.job_queue.get_jobs_by_name(f"break_{chat_id}_{target_sid}_{uid}")
    for job in jobs:
        job.schedule_removal()

    del sessions[target_sid]["breaks"][uid]
    save_data(data)

    await update.message.reply_text(
        f"💪 *{user.full_name}* خلّص استراحته بدري!\n"
        f"رجع يذاكر 📚🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /pomodoro ──────────────────────────────────────────────────────────────

async def cmd_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ المجموعات بس.")
        return

    await maybe_announce(context.bot, update.effective_chat.id)

    args = context.args
    cycles = int(args[0]) if args and args[0].isdigit() else 4
    cycles = max(1, min(cycles, 8))
    work_min = 25
    break_min = 5
    total = cycles * (work_min + break_min)

    session_id = f"p{datetime.now(TZ).strftime('%H%M%S')}{random.randint(100, 999)}"
    sessions = data["sessions"].setdefault(chat_id, {})

    sessions[session_id] = {
        "state": "waiting",
        "duration": total,
        "topic": None,
        "started_by": user.id,
        "participants": {str(user.id): {"name": user.full_name, "username": user.username or ""}},
        "start_time": None,
        "end_time": None,
        "breaks": {},
        "pomodoro": True,
        "pomo_cycles": cycles,
        "pomo_work": work_min,
        "pomo_break": break_min,
        "pomo_cycle": 0,
        "pinned_message_id": None,
        "active_pinned_message_id": None,
    }
    stats = get_stats(str(user.id))
    stats["sessions_joined"] += 1
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ انضم للبومودورو", callback_data=f"join_{chat_id}_{session_id}")]]
    msg = await update.message.reply_text(
        f"🍅 *سيشن بومودورو!*\n\n"
        f"👤 بدأها: {user.full_name}\n"
        f"🔄 الدورات: *{cycles}* × (25 دقيقة مذاكرة + 5 استراحة)\n"
        f"⏱ المجموع: *{fmt_duration(total)}*\n\n"
        f"🚀 هتبدأ على طول!\nاضغط للانضمام 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    sessions[session_id]["pinned_message_id"] = msg.message_id
    save_data(data)
    await pin_message(context.bot, update.effective_chat.id, msg.message_id)

    context.job_queue.run_once(
        start_session_job,
        when=2,
        data={"chat_id": chat_id, "session_id": session_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}_{session_id}",
    )

# ── /status ────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    sessions = data["sessions"].get(chat_id, {})

    active_sessions = [(s_id, s) for s_id, s in sessions.items() if s.get("state") in ("waiting", "active")]

    if not active_sessions:
        await update.message.reply_text(
            "💤 *مفيش سيشن شغالة.*\n\nابدأ واحدة بـ `/study <المدة>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    for s_id, session in active_sessions:
        participants = session["participants"]
        breaks = session.get("breaks", {})
        lines = []
        for uid, pinfo in participants.items():
            if uid in breaks:
                end_str = fmt_time(breaks[uid]["end"])
                lines.append(f" ☕ {pinfo['name']} — في استراحة (ترجع {end_str})")
            else:
                lines.append(f" 📖 {pinfo['name']} — بيذاكر")

        state_emoji = "⏳" if session["state"] == "waiting" else "🔒"
        state_text = "بتستنى مشاركين" if session["state"] == "waiting" else "شغالة"
        topic_line = f"\n📝 الموضوع: *{session['topic']}*" if session.get("topic") else ""

        # حساب الوقت المتبقي
        remaining_text = ""
        if session.get("end_time") and session["state"] == "active":
            try:
                end_dt = datetime.fromisoformat(session["end_time"])
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=TZ)
                remaining = (end_dt - now()).total_seconds()
                if remaining > 0:
                    remaining_min = int(remaining // 60)
                    remaining_text = f"\n⏳ الوقت المتبقي: *{fmt_duration(remaining_min)}*"
            except Exception:
                pass

        msg = (
            f"{state_emoji} *حالة السيشن ({state_text})*\n\n"
            f"⏱ المدة: *{fmt_duration(session['duration'])}*"
            f"{topic_line}"
        )
        if session.get("end_time"):
            msg += f"\n🏁 تنتهي الساعة: *{fmt_time(session['end_time'])}*"
        msg += remaining_text
        msg += f"\n\n*المشاركين ({len(lines)}):*\n" + "\n".join(lines)

        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ── /stats ─────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    stats = get_stats(uid)
    streak = data["streaks"].get(uid, {}).get("streak", 0)
    max_streak = data["streaks"].get(uid, {}).get("max_streak", 0)
    hours = stats["total_minutes"] // 60
    minutes = stats["total_minutes"] % 60

    badges = stats.get("badges", [])
    badges_text = " ".join(badges) if badges else "لسه مفيش شارات"

    # هدف اليومي
    goal = data["goals"].get(uid)
    goal_text = ""
    if goal:
        daily_min = stats.get("daily_minutes", 0)
        progress = min(100, int((daily_min / goal) * 100))
        filled = progress // 10
        bar = "█" * filled + "░" * (10 - filled)
        goal_text = f"\n\n🎯 *الهدف اليومي:*\n[{bar}] {progress}%\n_{fmt_duration(daily_min)}_ من أصل _{fmt_duration(goal)}_"

    await update.message.reply_text(
        f"📊 *إحصائياتك*\n\n"
        f"⏱ وقت المذاكرة الكلي: *{hours}س {minutes}د*\n"
        f"✅ سيشنات اكتملت: *{stats['sessions_completed']}*\n"
        f"👥 سيشنات انضممت ليها: *{stats['sessions_joined']}*\n"
        f"🔥 سلسلة الأيام: *{streak} يوم*\n"
        f"🏅 أعلى سلسلة: *{max_streak} يوم*\n"
        f"⭐ نقاطك الكلية: *{stats.get('points', 0)}*\n"
        f"📅 آخر يوم مذاكرة: *{stats.get('last_study_date') or 'غير متاح'}*\n"
        f"🎖 شاراتك: {badges_text}"
        f"{goal_text}",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /goal ──────────────────────────────────────────────────────────────────

async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    args = context.args

    if not args:
        current = data["goals"].get(uid)
        if current:
            await update.message.reply_text(
                f"🎯 هدفك اليومي الحالي: *{fmt_duration(current)}*\n\n"
                f"غيّره بـ `/goal <المدة>` مثلاً `/goal 2h`\nأو امسحه بـ `/goal clear`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                "🎯 مفيش هدف يومي. حدّده بـ `/goal <المدة>` مثلاً `/goal 2h`",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    if args[0].lower() == "clear":
        data["goals"].pop(uid, None)
        save_data(data)
        await update.message.reply_text("✅ تم مسح الهدف اليومي.")
        return

    duration = parse_duration(args[0])
    if not duration:
        await update.message.reply_text(
            "❌ مدة مش صحيحة. استخدم مثلاً `/goal 2h` أو `/goal 90m`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    data["goals"][uid] = duration
    save_data(data)
    await update.message.reply_text(
        f"✅ تم تحديد هدفك اليومي: *{fmt_duration(duration)}*\n\n"
        f"هتشوف تقدمك في /stats كل يوم 💪",
        parse_mode=ParseMode.MARKDOWN
    )

# ── /leaderboard & /weekly ─────────────────────────────────────────────────

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = build_leaderboard_text("🏆 ترتيب النقاط الكلية", sort_key="points")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = build_leaderboard_text("🏆 ترتيب الأسبوع", sort_key="weekly_points")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ── /badges ────────────────────────────────────────────────────────────────

async def cmd_badges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    stats = get_stats(uid)
    badges = stats.get("badges", [])

    all_badges = [
        ("🌱 مبتدئ", "أكمل سيشن واحدة"),
        ("📚 مذاكر", "أكمل 5 سيشنات"),
        ("🏃 مداوم", "أكمل 20 سيشن"),
        ("🏆 بطل", "أكمل 50 سيشن"),
        ("⏱ ساعة", "ذاكر ساعة كاملة"),
        ("🕐 10 ساعات", "ذاكر 10 ساعات"),
        ("🕑 50 ساعة", "ذاكر 50 ساعة"),
        ("🔥 3 أيام", "سلسلة 3 أيام متتالية"),
        ("🔥🔥 أسبوع", "سلسلة 7 أيام"),
        ("🔥🔥🔥 شهر", "سلسلة 30 يوم"),
        ("⭐ 100 نقطة", "اجمع 100 نقطة"),
        ("💎 1000 نقطة", "اجمع 1000 نقطة"),
    ]

    lines = []
    for badge_name, desc in all_badges:
        if badge_name in badges:
            lines.append(f"✅ {badge_name} — _{desc}_")
        else:
            lines.append(f"🔒 ~~{badge_name}~~ — _{desc}_")

    await update.message.reply_text(
        f"🎖 *شاراتك*\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN
    )

# ── /end ───────────────────────────────────────────────────────────────────

async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    sessions = data["sessions"].get(chat_id, {})
    target_session = None
    target_sid = None
    for s_id, s in sessions.items():
        if s.get("state") in ("waiting", "active") and uid in s.get("participants", {}):
            target_session = s
            target_sid = s_id
            break

    if not target_session:
        await update.message.reply_text("❌ مفيش سيشن شغالة تسيبها.")
        return

    del target_session["participants"][uid]
    save_data(data)

    await update.message.reply_text(
        f"👋 *{user.full_name}* ساب السيشن.\n"
        f"_المداومة هي المفتاح! شوفك المرة الجاية._ 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

    if len(target_session["participants"]) == 0:
        await check_and_end_empty_session(context, chat_id, target_sid)

# ── Guard messages ─────────────────────────────────────────────────────────

async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type == "private":
        return

    await maybe_announce(context.bot, update.effective_chat.id)

    chat_id = str(update.effective_chat.id)
    uid = str(update.effective_user.id)

    sessions = data["sessions"].get(chat_id, {})

    for s_id, session in sessions.items():
        if session.get("state") != "active":
            continue
        if uid not in session.get("participants", {}):
            continue
        if uid in session.get("breaks", {}):
            continue

        try:
            await update.message.delete()
            add_points(uid, -POINTS_PENALTY)
            save_data(data)

            mention = f"[{update.effective_user.first_name}](tg://user?id={uid})"
            stats = get_stats(uid)
            delete_msg = random.choice(DELETE_MESSAGES)
            await context.bot.send_message(
                update.effective_chat.id,
                f"📵 {mention} {delete_msg}\n"
                f"_اتخصم منك {POINTS_PENALTY} نقاط — نقاطك: {stats.get('points', 0)}_",
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError:
            pass
        break

# ── Daily leaderboard ──────────────────────────────────────────────────────

async def send_daily_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    # إعادة تصفير الدقائق اليومية
    for uid in data["stats"]:
        data["stats"][uid]["daily_minutes"] = 0
    save_data(data)

    for chat_id_str in list(data["sessions"].keys()):
        try:
            chat_int = int(chat_id_str)
            text = build_leaderboard_text("🏆 ترتيب اليوم — النقاط الكلية", sort_key="points")
            await context.bot.send_message(chat_int, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"فشل إرسال الترتيب اليومي لـ {chat_id_str}: {e}")

# ── Weekly leaderboard ─────────────────────────────────────────────────────

async def send_weekly_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    for chat_id_str in list(data["sessions"].keys()):
        try:
            chat_int = int(chat_id_str)
            text = build_leaderboard_text("🏆 ترتيب الأسبوع — النقاط الأسبوعية", sort_key="weekly_points")
            await context.bot.send_message(chat_int, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"فشل إرسال الترتيب الأسبوعي لـ {chat_id_str}: {e}")

    for uid in data["stats"]:
        data["stats"][uid]["weekly_points"] = 0
        data["stats"][uid]["weekly_minutes"] = 0
    save_data(data)

# ── Schedule recurring jobs ────────────────────────────────────────────────

def schedule_recurring_jobs(app):
    job_queue = app.job_queue

    cairo_now = now()
    midnight = cairo_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    seconds_until_midnight = (midnight - cairo_now).total_seconds()

    job_queue.run_repeating(
        send_daily_leaderboard,
        interval=86400,
        first=seconds_until_midnight,
        name="daily_leaderboard",
    )

    days_until_monday = (7 - cairo_now.weekday()) % 7 or 7
    next_monday = cairo_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
    seconds_until_monday = (next_monday - cairo_now).total_seconds()

    job_queue.run_repeating(
        send_weekly_leaderboard,
        interval=604800,
        first=seconds_until_monday,
        name="weekly_leaderboard",
    )

# ── /start ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *StudyLock Bot*\n\n"
        "بوت لتنظيم جلسات المذاكرة في المجموعات.\n\n"
        "━━━ *أوامر السيشن* ━━━\n"
        "/study 1h — ابدأ سيشن ساعة\n"
        "/study 1h30m الرياضيات — سيشن بموضوع محدد\n"
        "/pomodoro — سيشن بومودورو (25+5)\n"
        "/pomodoro 6 — بومودورو بعدد دورات مخصص\n"
        "/break 10 — استراحة 10 دقايق\n"
        "/back — خلّص استراحتك بدري\n"
        "/end — اخرج من السيشن\n"
        "/status — حالة السيشن + الوقت المتبقي\n\n"
        "━━━ *إحصائيات وترتيب* ━━━\n"
        "/stats — إحصائياتك الكاملة\n"
        "/leaderboard — الترتيب الكلي\n"
        "/weekly — ترتيب الأسبوع\n"
        "/badges — شاراتك وإنجازاتك\n"
        "/goal 2h — حدد هدفك اليومي\n\n"
        "━━━ *أوامر الأدمن* ━━━\n"
        "/reset — تنظيف السيشنات (أدمن فقط)\n\n"
        "_ضيفني في المجموعة واعملني أدمن!_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN مش متحدد.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("study", cmd_study))
    app.add_handler(CommandHandler("pomodoro", cmd_pomodoro))
    app.add_handler(CommandHandler("break", cmd_break))
    app.add_handler(CommandHandler("back", cmd_back))
    app.add_handler(CommandHandler("end", cmd_end))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("weekly", cmd_weekly))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("goal", cmd_goal))
    app.add_handler(CommandHandler("badges", cmd_badges))
    app.add_handler(CallbackQueryHandler(join_callback, pattern=r"^join_"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, guard_messages))

    async def post_init(application):
        schedule_recurring_jobs(application)

    app.post_init = post_init

    logger.info("StudyLock Bot شغال...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
