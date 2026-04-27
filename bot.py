import os
import asyncio
import logging
import json
import random
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
LOCKED = ChatPermissions(can_send_messages=False)
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
    return {"sessions": {}, "stats": {}, "streaks": {}, "goals": {}, "points": {}, "violations": {}}

def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, default=str))

data = load_data()

# ── Messages ───────────────────────────────────────────────────────────────
MOTIVATIONAL = [
    "⚡ استنى الآخر — كل دقيقة بتفرق!",
    "📖 دلوقتي بتبني مستقبلك. ماتوقفش!",
    "🔥 الاستمرار أهم من الشدة. خليك في الزون!",
    "🧠 دماغك بتتقوى مع كل صفحة. استمر!",
    "💪 البطل بيتصنع في لحظات زي دي!",
    "🌟 ساعة أقرب للهدف. انت عارفها!",
    "🎯 ركز. اتنفس. كمّل.",
    "🚀 المجهود اللي بتبذله النهارده هيرجع عليك بكره.",
    "📚 كل اللي بيتعبوا دلوقتي هيفرحوا بكره!",
    "🏆 المذاكرة مش عذاب — هي استثمار في نفسك!",
]

BREAK_OVER = [
    "☕ خلص الاستراحة! ارجع تذاكر يسطا 📚",
    "⏰ قومي يا نايمة! الاستراحة خلصت 💪",
    "🔔 يلا يلا — البريك انتهى! 🚀",
    "📚 اشحن خلص؟ يلا ارجع الزون! 🔒",
    "⚡ الاستراحة ولّت — الشدة جت! 🔥",
]

GUARD_REPLIES = [
    "📵 {name} كمّل مذاكرة يسطا! 🔒",
    "🤫 {name} مش وقت كلام — وقت مذاكرة! 📖",
    "🙈 {name} ركز ركز ركز! 🔥",
    "📵 {name} التليفون بعدين! 🔒",
    "🧠 {name} المذاكرة مش بتمذاكر نفسها! 💪",
]

BADGES = {
    "first_session": ("🎖️", "أول سيشن"),
    "streak_3": ("🔥", "3 أيام متتالية"),
    "streak_7": ("⚡", "أسبوع كامل"),
    "streak_30": ("👑", "شهر كامل"),
    "hours_5": ("📚", "5 ساعات"),
    "hours_10": ("🏅", "10 ساعات"),
    "hours_50": ("🏆", "50 ساعة"),
    "sessions_10": ("✨", "10 سيشنات"),
    "sessions_50": ("💎", "50 سيشن"),
}

# ── Points config ──────────────────────────────────────────────────────────
# نقاط بتتكسب
POINTS_SESSION_COMPLETE  = 50   # إكمال سيشن كامل
POINTS_PER_HOUR          = 20   # لكل ساعة مذاكرة
POINTS_STREAK_BONUS      = 10   # بونص لكل يوم في الـ streak
POINTS_GOAL_SET          = 5    # تحديد هدف
POINTS_NO_VIOLATION      = 15   # إتمام سيشن بدون مخالفة واحدة

# نقاط بتتخصم
POINTS_PENALTY_PER_MSG   = 5    # كل رسالة أثناء السيشن
POINTS_PENALTY_MAX       = 30   # أقصى خصم في سيشن واحدة (عشان محدش يوصل سالب كتير)

# ── Utility ────────────────────────────────────────────────────────────────
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
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    elif h:
        return f"{h}h"
    return f"{m}m"

def parse_duration(text: str) -> int | None:
    import re
    text = text.lower().strip()
    match = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m?)?', text)
    if not match or not any(match.groups()):
        return None
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    total = h * 60 + m
    return total if 10 <= total <= 480 else None

def get_session(chat_id: str) -> dict | None:
    return data["sessions"].get(chat_id)

def get_stats(user_id: str) -> dict:
    return data["stats"].setdefault(user_id, {
        "total_minutes": 0, "sessions_completed": 0,
        "sessions_joined": 0, "last_study_date": None,
        "username": "", "name": "", "badges": [],
    })

def update_streak(user_id: str):
    s = data["streaks"].setdefault(user_id, {"streak": 0, "last_date": None})
    today = now().date().isoformat()
    if s["last_date"] == today:
        return
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    if s["last_date"] == yesterday:
        s["streak"] += 1
    else:
        s["streak"] = 1
    s["last_date"] = today

def check_and_award_badges(user_id: str) -> list[str]:
    """Check if user earned new badges and return list of new badge texts."""
    stats = get_stats(user_id)
    streak = data["streaks"].get(user_id, {}).get("streak", 0)
    earned = stats.setdefault("badges", [])
    new_badges = []

    checks = [
        ("first_session", stats["sessions_completed"] >= 1),
        ("streak_3", streak >= 3),
        ("streak_7", streak >= 7),
        ("streak_30", streak >= 30),
        ("hours_5", stats["total_minutes"] >= 300),
        ("hours_10", stats["total_minutes"] >= 600),
        ("hours_50", stats["total_minutes"] >= 3000),
        ("sessions_10", stats["sessions_completed"] >= 10),
        ("sessions_50", stats["sessions_completed"] >= 50),
    ]

    for badge_id, condition in checks:
        if condition and badge_id not in earned:
            earned.append(badge_id)
            icon, label = BADGES[badge_id]
            new_badges.append(f"{icon} {label}")

    return new_badges

def is_admin(member) -> bool:
    return member.status in ("administrator", "creator")

# ── Points helpers ─────────────────────────────────────────────────────────
def get_points(user_id: str) -> dict:
    """يرجع بيانات نقاط اليوزر، وبيعمل الـ structure لو مش موجود."""
    today = now().date().isoformat()
    week  = now().isocalendar()
    week_key = f"{week.year}-W{week.week:02d}"

    p = data["points"].setdefault(user_id, {
        "total": 0,
        "today": 0,
        "today_date": today,
        "this_week": 0,
        "week_key": week_key,
        "name": "",
    })

    # Reset daily counter لو يوم جديد
    if p.get("today_date") != today:
        p["today"] = 0
        p["today_date"] = today

    # Reset weekly counter لو أسبوع جديد
    if p.get("week_key") != week_key:
        p["this_week"] = 0
        p["week_key"] = week_key

    return p

def add_points(user_id: str, amount: int, name: str = ""):
    """أضف نقاط لليوزر."""
    p = get_points(user_id)
    if name:
        p["name"] = name
    p["total"]     = max(0, p["total"]     + amount)
    p["today"]     = max(0, p["today"]     + amount)
    p["this_week"] = max(0, p["this_week"] + amount)

def deduct_points(user_id: str, amount: int, name: str = "") -> int:
    """اخصم نقاط من اليوزر. بيرجع الخصم الفعلي."""
    p = get_points(user_id)
    if name:
        p["name"] = name
    actual = min(amount, p["total"])   # ماتخصمش أكتر من اللي عنده
    p["total"]     = max(0, p["total"]     - actual)
    p["today"]     = max(0, p["today"]     - actual)
    p["this_week"] = max(0, p["this_week"] - actual)
    return actual

def get_session_violations(chat_id: str, user_id: str) -> int:
    """عدد مخالفات اليوزر في السيشن الحالية."""
    return data["violations"].get(f"{chat_id}_{user_id}", 0)

def add_violation(chat_id: str, user_id: str):
    """سجّل مخالفة جديدة وارجع عدد المخالفات."""
    key = f"{chat_id}_{user_id}"
    data["violations"][key] = data["violations"].get(key, 0) + 1
    return data["violations"][key]

def clear_violations(chat_id: str):
    """امسح مخالفات السيشن بعد ما تخلص."""
    keys_to_del = [k for k in data["violations"] if k.startswith(f"{chat_id}_")]
    for k in keys_to_del:
        del data["violations"][k]

def award_session_points(chat_id: str, session: dict):
    """وزّع نقاط نهاية السيشن على المشاركين وارجع dict بالتفاصيل."""
    results = {}
    duration = session["duration"]
    hours_bonus = (duration // 60) * POINTS_PER_HOUR

    for uid, pinfo in session["participants"].items():
        name = pinfo["name"]
        earned = POINTS_SESSION_COMPLETE + hours_bonus

        # بونص الـ streak
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        streak_bonus = streak * POINTS_STREAK_BONUS
        earned += streak_bonus

        # بونص لو ما اخطاش خطأ
        violations = get_session_violations(chat_id, uid)
        no_violation_bonus = POINTS_NO_VIOLATION if violations == 0 else 0
        earned += no_violation_bonus

        # الخصومات اللي حصلت أثناء السيشن (محسوبة مسبقاً في guard)
        penalty = min(violations * POINTS_PENALTY_PER_MSG, POINTS_PENALTY_MAX)

        net = earned - penalty
        add_points(uid, net, name)

        results[uid] = {
            "name": name,
            "earned": earned,
            "penalty": penalty,
            "net": net,
            "violations": violations,
            "streak_bonus": streak_bonus,
            "no_violation_bonus": no_violation_bonus,
        }

    save_data(data)
    return results

def build_points_board(scope: str, top_n: int = 5) -> list[tuple[str, str, int]]:
    """
    ابني ليدربورد النقاط.
    scope: 'today' | 'this_week' | 'total'
    يرجع list من (uid, name, points) مرتبة تنازلياً.
    """
    today    = now().date().isoformat()
    week     = now().isocalendar()
    week_key = f"{week.year}-W{week.week:02d}"

    results = []
    for uid, p in data["points"].items():
        # تأكد إن الـ counters محدثة
        if scope == "today":
            pts = p.get("today", 0) if p.get("today_date") == today else 0
        elif scope == "this_week":
            pts = p.get("this_week", 0) if p.get("week_key") == week_key else 0
        else:
            pts = p.get("total", 0)

        name = p.get("name") or f"User {uid}"
        if pts > 0:
            results.append((uid, name, pts))

    results.sort(key=lambda x: x[2], reverse=True)
    return results[:top_n]

# ── Lock/Unlock helpers ────────────────────────────────────────────────────
async def lock_chat(bot, chat_id: int):
    try:
        await bot.set_chat_permissions(chat_id, LOCKED)
    except TelegramError as e:
        logger.warning(f"Lock failed: {e}")

async def unlock_chat(bot, chat_id: int):
    try:
        await bot.set_chat_permissions(chat_id, UNLOCKED)
    except TelegramError as e:
        logger.warning(f"Unlock failed: {e}")

async def restrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, LOCKED)
    except TelegramError as e:
        logger.warning(f"Restrict failed: {e}")

async def unrestrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, UNLOCKED)
    except TelegramError as e:
        logger.warning(f"Unrestrict failed: {e}")

# ── /start ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *StudyLock Bot*\n\n"
        "بساعد مجموعات المذاكرة تركز عن طريق قفل الشات أثناء السيشن! 🔒\n\n"
        "*الأوامر:*\n"
        "`/study 2h` — ابدأ سيشن (مثال: 2h، 90m، 1h30m)\n"
        "`/pomodoro [cycles]` — سيشن بوميدورو (25م شغل + 5م استراحة)\n"
        "`/schedule HH:MM 2h` — جدولة سيشن بوقت معين\n"
        "`/goal <النص>` — حدد هدفك للسيشن\n"
        "`/pause` — وقفة مؤقتة للسيشن (أدمن)\n"
        "`/resume` — استأنف السيشن (أدمن)\n"
        "`/break 10` — استراحة 10 دقايق\n"
        "`/back` — ارجع من الاستراحة بدري\n"
        "`/kick @user` — اطرد مشارك من السيشن (أدمن)\n"
        "`/status` — شوف حالة السيشن الحالية\n"
        "`/stats` — إحصائياتك الشخصية\n"
        "`/groupstats` — إحصائيات المجموعة\n"
        "`/leaderboard` — ترتيب المجموعة بالوقت\n"
        "`/end` — اخرج من السيشن\n"
        "`/reset` — امسح سيشن عالقة (أدمن)\n\n"
        "*🪙 نظام النقاط:*\n"
        "`/points` — شوف رصيدك ومصادر النقاط\n"
        "`/pointsboard` — أفضل 5 النهارده\n"
        "`/pointsboard week` — أفضل 5 الأسبوع\n"
        "`/pointsboard total` — أفضل 5 الكل\n\n"
        "📅 ترتيب اليوم بيتبعت تلقائياً آخر كل يوم\n"
        "📆 ترتيب الأسبوع بيتبعت تلقائياً كل أحد\n\n"
        "_ضيفني على المجموعة واعملني أدمن عشان اشتغل!_ 🚀",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /study ─────────────────────────────────────────────────────────────────
async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في المجموعات بس.")
        return

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active", "paused"):
        await update.message.reply_text("⚠️ في سيشن شغالة دلوقتي!\nاستخدم /status تشوف التفاصيل.")
        return

    if not context.args:
        await update.message.reply_text(
            "📚 *الاستخدام:* `/study <المدة>`\n\n"
            "أمثلة:\n`/study 2h` — ساعتين\n`/study 90m` — 90 دقيقة\n`/study 1h30m` — ساعة ونص",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    duration = parse_duration(context.args[0])
    if not duration:
        await update.message.reply_text(
            "❌ مدة غلط. استخدم: `2h`، `90m`، `1h30m`\n"
            "الحد الأدنى: 10 دقايق | الحد الأقصى: 8 ساعات.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    join_deadline = (now() + timedelta(minutes=5)).isoformat()
    data["sessions"][chat_id] = {
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
        "goal": None,
        "paused_at": None,
        "paused_elapsed": 0,
    }

    stats = get_stats(str(user.id))
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    await update.message.reply_text(
        f"📚 *سيشن مذاكرة جديدة!*\n\n"
        f"👤 بدأها: {user.full_name}\n"
        f"⏱ المدة: *{fmt_duration(duration)}*\n"
        f"👥 المشاركين حالياً: 1\n\n"
        f"⏳ *عندك 5 دقايق تنضم!*\n"
        f"السيشن هتبدأ الساعة: *{fmt_time(join_deadline)}*\n\n"
        f"اضغط الزرار تنضم 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.job_queue.run_once(
        start_session_job,
        when=300,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}",
    )

# ── /schedule ──────────────────────────────────────────────────────────────
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في المجموعات بس.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "🗓 *الاستخدام:* `/schedule HH:MM <المدة>`\n\n"
            "مثال: `/schedule 22:00 2h` — سيشن الساعة 10 بالليل لمدة ساعتين",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        time_parts = context.args[0].split(":")
        hour, minute = int(time_parts[0]), int(time_parts[1])
        target = now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now():
            target += timedelta(days=1)
    except Exception:
        await update.message.reply_text("❌ صيغة الوقت غلط. استخدم HH:MM زي `22:00`.")
        return

    duration = parse_duration(context.args[1])
    if not duration:
        await update.message.reply_text("❌ مدة غلط. استخدم مثلاً `2h` أو `90m`.")
        return

    delay = (target - now()).total_seconds()
    time_str = target.strftime("%I:%M %p")

    context.job_queue.run_once(
        announce_scheduled_session_job,
        when=delay,
        data={
            "chat_id": chat_id,
            "chat_int": update.effective_chat.id,
            "duration": duration,
            "started_by": user.id,
            "started_by_name": user.full_name,
        },
        name=f"scheduled_{chat_id}",
    )

    await update.message.reply_text(
        f"🗓 *سيشن اتجدولت!*\n\n"
        f"👤 بدأها: {user.full_name}\n"
        f"⏰ موعد البداية: *{time_str}*\n"
        f"⏱ المدة: *{fmt_duration(duration)}*\n\n"
        f"هيجيلكم إشعار قبل بداية السيشن بـ 5 دقايق! 🔔",
        parse_mode=ParseMode.MARKDOWN,
    )

async def announce_scheduled_session_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    chat_id = d["chat_id"]
    chat_int = d["chat_int"]
    duration = d["duration"]
    started_by_name = d["started_by_name"]
    started_by = d["started_by"]

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active", "paused"):
        return

    join_deadline = (now() + timedelta(minutes=5)).isoformat()
    data["sessions"][chat_id] = {
        "state": "waiting",
        "duration": duration,
        "started_by": started_by,
        "participants": {},
        "join_deadline": join_deadline,
        "start_time": None,
        "end_time": None,
        "breaks": {},
        "pomodoro": False,
        "pomo_cycle": 0,
        "goal": None,
        "paused_at": None,
        "paused_elapsed": 0,
    }
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    await context.bot.send_message(
        chat_int,
        f"⏰ *جه وقت السيشن المجدولة!*\n\n"
        f"👤 بدأها: {started_by_name}\n"
        f"⏱ المدة: *{fmt_duration(duration)}*\n\n"
        f"⏳ *عندك 5 دقايق تنضم!*\n"
        f"اضغط الزرار 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.job_queue.run_once(
        start_session_job,
        when=300,
        data={"chat_id": chat_id, "chat_int": chat_int},
        name=f"start_{chat_id}",
    )

# ── /goal ──────────────────────────────────────────────────────────────────
async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    uid = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text(
            "🎯 *الاستخدام:* `/goal <هدفك>`\n\nمثال: `/goal حل 3 فصول رياضيات`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    goal_text = " ".join(context.args)

    # Save personal goal
    data["goals"][uid] = {
        "text": goal_text,
        "set_at": now().isoformat(),
        "chat_id": chat_id,
        "done": False,
    }

    # Also attach to session if active
    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active"):
        if "goals" not in session:
            session["goals"] = {}
        session["goals"][uid] = goal_text

    save_data(data)

    await update.message.reply_text(
        f"🎯 *هدفك اتسجل!*\n\n"
        f"📝 الهدف: _{goal_text}_\n\n"
        f"ركز وحققه! 💪🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Join callback ──────────────────────────────────────────────────────────
async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    _, chat_id = query.data.split("_", 1)

    session = get_session(chat_id)
    if not session or session["state"] != "waiting":
        await query.answer("❌ السيشن دي مش متاحة للانضمام.", show_alert=True)
        return

    uid = str(user.id)
    if uid in session["participants"]:
        await query.answer("✅ انت منضم أصلاً!", show_alert=True)
        return

    session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
    stats = get_stats(uid)
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    names = [p["name"] for p in session["participants"].values()]
    await query.answer("✅ انضممت للسيشن! يلا بينا 💪", show_alert=True)

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    await query.edit_message_text(
        f"📚 *سيشن مذاكرة — غرفة الانتظار*\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"👥 المشاركين ({len(names)}): {', '.join(names)}\n\n"
        f"⏳ السيشن هتبدأ في أقل من 5 دقايق!\n"
        f"اضغط الزرار للانضمام 👇",
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

    if not session["participants"]:
        data["sessions"].pop(chat_id, None)
        save_data(data)
        await context.bot.send_message(chat_int, "😴 السيشن اتلغت — مفيش حد انضم.")
        return

    start = now()
    end = start + timedelta(minutes=session["duration"])
    session["state"] = "active"
    session["start_time"] = start.isoformat()
    session["end_time"] = end.isoformat()
    save_data(data)

    participants = session["participants"]
    names = [p["name"] for p in participants.values()]

    await lock_chat(context.bot, chat_int)

    # Build goal summary if any
    goals_section = ""
    if session.get("goals"):
        goal_lines = [f"  • {pname}: _{gtext}_"
                      for uid, gtext in session["goals"].items()
                      if (pname := session["participants"].get(uid, {}).get("name"))]
        if goal_lines:
            goals_section = "\n\n🎯 *أهداف السيشن:*\n" + "\n".join(goal_lines)

    pomo_section = ""
    if session.get("pomodoro"):
        cycles = session.get("pomo_cycles", 4)
        pomo_section = f"\n🍅 وضع بوميدورو: *{cycles} سايكل* (25م + 5م)"

    await context.bot.send_message(
        chat_int,
        f"🔒 *السيشن بدأت!*\n\n"
        f"👥 المشاركين: {', '.join(names)}\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"🏁 بتخلص الساعة: *{fmt_time(end.isoformat())}*"
        f"{pomo_section}"
        f"{goals_section}\n\n"
        f"📵 *الشات اتقفل.* وضع تركيز ON!\n"
        f"_استخدم /break <دقايق> بعد السيشن._",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Hourly motivation
    for i in range(1, session["duration"] // 60 + 1):
        msg = MOTIVATIONAL[i % len(MOTIVATIONAL)]
        context.job_queue.run_once(
            send_motivation_job,
            when=i * 3600,
            data={"chat_int": chat_int, "msg": msg, "elapsed_min": i * 60, "total": session["duration"]},
            name=f"motiv_{chat_id}_{i}",
        )

    # 10-min warning before end
    if session["duration"] > 10:
        context.job_queue.run_once(
            warning_job,
            when=(session["duration"] - 10) * 60,
            data={"chat_int": chat_int},
            name=f"warn_{chat_id}",
        )

    # End job
    context.job_queue.run_once(
        end_session_job,
        when=session["duration"] * 60,
        data={"chat_id": chat_id, "chat_int": chat_int},
        name=f"end_{chat_id}",
    )

# ── Warning job ────────────────────────────────────────────────────────────
async def warning_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        context.job.data["chat_int"],
        "⚠️ *تنبيه!* باقي *10 دقايق* على نهاية السيشن!\n"
        "كملوا قوي يلا! 💪🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Motivation job ─────────────────────────────────────────────────────────
async def send_motivation_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    elapsed = fmt_duration(d["elapsed_min"])
    remaining = fmt_duration(d["total"] - d["elapsed_min"])
    await context.bot.send_message(
        d["chat_int"],
        f"{d['msg']}\n\n"
        f"⏱ مضى: *{elapsed}* | باقي: *{remaining}*",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── End session job ────────────────────────────────────────────────────────
async def end_session_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    chat_int = job_data["chat_int"]

    session = get_session(chat_id)
    if not session:
        return

    for uid in session["participants"]:
        stats = get_stats(uid)
        stats["total_minutes"] += session["duration"]
        stats["sessions_completed"] += 1
        stats["last_study_date"] = now().date().isoformat()
        update_streak(uid)

    session["state"] = "ended"
    save_data(data)

    await unlock_chat(context.bot, chat_int)

    # وزّع النقاط على المشاركين
    points_results = award_session_points(chat_id, session)

    lines = []
    badges_lines = []
    for uid, pinfo in session["participants"].items():
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        streak_str = f" 🔥{streak}" if streak > 1 else ""
        pr = points_results.get(uid, {})
        net      = pr.get("net", 0)
        earned   = pr.get("earned", 0)
        penalty  = pr.get("penalty", 0)
        viol     = pr.get("violations", 0)
        viol_str = f" ⚠️{viol} مخالفة" if viol > 0 else " ✨بدون مخالفات"
        total_pts = get_points(uid).get("total", 0)
        lines.append(
            f"  • *{pinfo['name']}*{streak_str}{viol_str}\n"
            f"    ➕{earned} ➖{penalty} = *+{net} نقطة* | رصيد: *{total_pts}* 🪙"
        )

        new_badges = check_and_award_badges(uid)
        if new_badges:
            badges_lines.append(f"  🎖️ {pinfo['name']}: {', '.join(new_badges)}")

    save_data(data)
    # امسح سجل المخالفات
    clear_violations(chat_id)

    badges_section = ""
    if badges_lines:
        badges_section = "\n\n🏆 *شارات جديدة!*\n" + "\n".join(badges_lines)

    await context.bot.send_message(
        chat_int,
        f"✅ *السيشن خلصت!*\n\n"
        f"🎉 أنتو ولادي! عملتوها!\n"
        f"⏱ وقت المذاكرة: *{fmt_duration(session['duration'])}*\n\n"
        f"*💰 النقاط:*\n" + "\n".join(lines) +
        badges_section +
        f"\n\n💤 الشات *اتفتح*. استاهلتوا الراحة!\n"
        f"_/points رصيدك | /pointsboard ترتيب اليوم_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /pause ─────────────────────────────────────────────────────────────────
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
    if not is_admin(member):
        await update.message.reply_text("❌ الأمر ده للأدمن بس.")
        return

    session = get_session(chat_id)
    if not session or session["state"] != "active":
        await update.message.reply_text("❌ مفيش سيشن شغالة دلوقتي.")
        return

    session["state"] = "paused"
    session["paused_at"] = now().isoformat()

    # Cancel scheduled jobs
    for job_name in [f"end_{chat_id}", f"warn_{chat_id}"]:
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

    save_data(data)
    await unlock_chat(context.bot, update.effective_chat.id)

    await update.message.reply_text(
        f"⏸ *السيشن اتوقفت مؤقتاً!*\n\n"
        f"الشات اتفتح لحد ما تيجي تكمل.\n"
        f"استخدم /resume لاستكمال السيشن. 🔄",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /resume ────────────────────────────────────────────────────────────────
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
    if not is_admin(member):
        await update.message.reply_text("❌ الأمر ده للأدمن بس.")
        return

    session = get_session(chat_id)
    if not session or session["state"] != "paused":
        await update.message.reply_text("❌ مفيش سيشن متوقفة دلوقتي.")
        return

    # Calculate remaining time
    paused_at = datetime.fromisoformat(session["paused_at"])
    if paused_at.tzinfo is None:
        paused_at = paused_at.replace(tzinfo=TZ)
    paused_elapsed_extra = int((now() - paused_at).total_seconds() / 60)

    original_end = datetime.fromisoformat(session["end_time"])
    if original_end.tzinfo is None:
        original_end = original_end.replace(tzinfo=TZ)

    new_end = original_end + timedelta(minutes=paused_elapsed_extra)
    remaining_min = int((new_end - now()).total_seconds() / 60)

    if remaining_min <= 0:
        session["state"] = "ended"
        save_data(data)
        await update.message.reply_text("⚠️ الوقت خلص أثناء الوقفة. السيشن انتهت.")
        return

    session["state"] = "active"
    session["end_time"] = new_end.isoformat()
    session["paused_at"] = None
    save_data(data)

    await lock_chat(context.bot, update.effective_chat.id)

    await update.message.reply_text(
        f"▶️ *السيشن اتكملت!*\n\n"
        f"⏱ الوقت الباقي: *{fmt_duration(remaining_min)}*\n"
        f"🏁 بتخلص الساعة: *{fmt_time(new_end.isoformat())}*\n\n"
        f"📵 الشات اتقفل تاني. يلا نكمل! 🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

    if remaining_min > 10:
        context.job_queue.run_once(
            warning_job,
            when=(remaining_min - 10) * 60,
            data={"chat_int": update.effective_chat.id},
            name=f"warn_{chat_id}",
        )

    context.job_queue.run_once(
        end_session_job,
        when=remaining_min * 60,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id},
        name=f"end_{chat_id}",
    )

# ── /break ─────────────────────────────────────────────────────────────────
async def cmd_break(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في المجموعات بس.")
        return

    session = get_session(chat_id)
    if not session or session["state"] not in ("ended", "active"):
        await update.message.reply_text("❌ مفيش سيشن حديثة. ابدأ واحدة بـ /study.")
        return

    if uid not in session["participants"]:
        await update.message.reply_text("❌ انت مكنتش في السيشن دي.")
        return

    if uid in session.get("breaks", {}):
        end_str = fmt_time(session["breaks"][uid]["end"])
        await update.message.reply_text(
            f"☕ انت أصلاً في استراحة!\n"
            f"بتخلص الساعة *{end_str}*. أو استخدم /back ترجع بدري.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if not context.args:
        await update.message.reply_text(
            "☕ *الاستخدام:* `/break <دقايق>`\n\nأمثلة: `/break 10` أو `/break 15`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        minutes = int(context.args[0])
        if not 1 <= minutes <= 60:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ مدة الاستراحة لازم تكون بين 1 و 60 دقيقة.")
        return

    break_end = now() + timedelta(minutes=minutes)
    session["breaks"][uid] = {
        "end": break_end.isoformat(),
        "duration": minutes,
        "name": user.full_name,
    }
    save_data(data)

    await restrict_user(context.bot, update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"☕ *استراحة {user.full_name} بدأت!*\n\n"
        f"⏱ المدة: *{minutes} دقيقة*\n"
        f"🔔 هيجيلك تنبيه الساعة: *{fmt_time(break_end.isoformat())}*\n\n"
        f"_استرخي واشحن — الشات متقفل عليك لحد كده! 😴_",
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
        save_data(data)

    await unrestrict_user(context.bot, chat_int, int(uid))

    mention = f"[{name}](tg://user?id={uid})"
    msg = random.choice(BREAK_OVER)
    await context.bot.send_message(
        chat_int,
        f"{msg}\n\n{mention}، الاستراحة خلصت — ارجع تذاكر! 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

    session = get_session(chat_id)
    if session and session.get("state") == "active":
        await restrict_user(context.bot, chat_int, int(uid))
        await context.bot.send_message(
            chat_int,
            f"🔒 {mention} رجع يذاكر! الشات اتقفل عليه تاني. 💪",
            parse_mode=ParseMode.MARKDOWN,
        )

# ── /back ──────────────────────────────────────────────────────────────────
async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    session = get_session(chat_id)
    if not session or uid not in session.get("breaks", {}):
        await update.message.reply_text("❌ مفيش استراحة شغالة عندك.")
        return

    for job in context.job_queue.get_jobs_by_name(f"break_{chat_id}_{uid}"):
        job.schedule_removal()

    del session["breaks"][uid]
    save_data(data)

    await unrestrict_user(context.bot, update.effective_chat.id, user.id)

    if session.get("state") == "active":
        await restrict_user(context.bot, update.effective_chat.id, user.id)

    await update.message.reply_text(
        f"💪 *{user.full_name}* رجع من الاستراحة بدري!\n"
        f"يلا نكمل نذاكر 📚🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /kick ──────────────────────────────────────────────────────────────────
async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
    if not is_admin(member):
        await update.message.reply_text("❌ الأمر ده للأدمن بس.")
        return

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ مفيش سيشن شغالة.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "👤 *ازاي تستخدم /kick:*\n\n"
            "رد على رسالة الشخص اللي عايز تطرده وابعت `/kick`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    target = update.message.reply_to_message.from_user
    target_uid = str(target.id)

    if target_uid not in session["participants"]:
        await update.message.reply_text("❌ الشخص ده مش في السيشن.")
        return

    del session["participants"][target_uid]

    if session["state"] == "active":
        await unrestrict_user(context.bot, update.effective_chat.id, target.id)

    save_data(data)

    await update.message.reply_text(
        f"👢 *{target.full_name}* اتطرد من السيشن بواسطة الأدمن.\n"
        f"_الشات اتفتح ليه._",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /pomodoro ──────────────────────────────────────────────────────────────
async def cmd_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في المجموعات بس.")
        return

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active", "paused"):
        await update.message.reply_text("⚠️ في سيشن شغالة دلوقتي.")
        return

    cycles = int(context.args[0]) if context.args and context.args[0].isdigit() else 4
    cycles = max(1, min(cycles, 8))
    work_min, break_min = 25, 5
    total = cycles * (work_min + break_min)

    join_deadline = (now() + timedelta(minutes=5)).isoformat()
    data["sessions"][chat_id] = {
        "state": "waiting",
        "duration": total,
        "started_by": user.id,
        "participants": {str(user.id): {"name": user.full_name, "username": user.username or ""}},
        "join_deadline": join_deadline,
        "start_time": None, "end_time": None, "breaks": {},
        "pomodoro": True,
        "pomo_cycles": cycles,
        "pomo_work": work_min,
        "pomo_break": break_min,
        "pomo_cycle": 0,
        "goal": None, "paused_at": None, "paused_elapsed": 0,
    }

    stats = get_stats(str(user.id))
    stats["sessions_joined"] += 1
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ انضم بوميدورو", callback_data=f"join_{chat_id}")]]
    await update.message.reply_text(
        f"🍅 *سيشن بوميدورو!*\n\n"
        f"👤 بدأها: {user.full_name}\n"
        f"🔄 سايكلات: *{cycles}* × (25م شغل + 5م استراحة)\n"
        f"⏱ المجموع: *{fmt_duration(total)}*\n\n"
        f"⏳ *5 دقايق للانضمام!*\n"
        f"اضغط الزرار 👇",
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

    if not session or session["state"] not in ("waiting", "active", "paused"):
        await update.message.reply_text(
            "💤 *مفيش سيشن شغالة دلوقتي.*\n\nابدأ واحدة بـ `/study <مدة>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    participants = session["participants"]
    breaks = session.get("breaks", {})
    goals = session.get("goals", {})

    lines = []
    for uid, pinfo in participants.items():
        if uid in breaks:
            end_str = fmt_time(breaks[uid]["end"])
            lines.append(f"  ☕ {pinfo['name']} — استراحة (يرجع {end_str})")
        else:
            goal_str = f' 🎯 _{goals[uid]}_' if uid in goals else ""
            lines.append(f"  📖 {pinfo['name']} — بيذاكر{goal_str}")

    state_map = {"waiting": "⏳ بتستنى", "active": "🔒 شغالة", "paused": "⏸ متوقفة"}
    state_text = state_map.get(session["state"], "")

    msg = (
        f"{state_text} *حالة السيشن*\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
    )
    if session.get("end_time"):
        msg += f"🏁 بتخلص: *{fmt_time(session['end_time'])}*\n"
    if session.get("paused_at"):
        msg += f"⏸ اتوقفت: *{fmt_time(session['paused_at'])}*\n"
    msg += f"\n*المشاركين ({len(lines)}):*\n" + "\n".join(lines)

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ── /stats ─────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    stats = get_stats(uid)
    streak = data["streaks"].get(uid, {}).get("streak", 0)
    h, m = divmod(stats["total_minutes"], 60)
    badges = stats.get("badges", [])
    badges_str = " ".join(BADGES[b][0] for b in badges if b in BADGES) or "لسه ماكسبتش شارات"

    goal = data["goals"].get(uid)
    goal_str = f"\n🎯 هدفك الحالي: _{goal['text']}_" if goal and not goal.get("done") else ""

    await update.message.reply_text(
        f"📊 *إحصائياتك*\n\n"
        f"⏱ إجمالي وقت المذاكرة: *{h}h {m}m*\n"
        f"✅ سيشنات اكتملت: *{stats['sessions_completed']}*\n"
        f"👥 سيشنات انضممت: *{stats['sessions_joined']}*\n"
        f"🔥 streak حالي: *{streak} يوم{'s' if streak != 1 else ''}*\n"
        f"📅 آخر يوم مذاكرة: *{stats.get('last_study_date') or 'N/A'}*\n"
        f"🏅 شاراتك: {badges_str}"
        f"{goal_str}",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /groupstats ────────────────────────────────────────────────────────────
async def cmd_groupstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not data["stats"]:
        await update.message.reply_text("📊 لسه مفيش بيانات! ابدأ سيشن أول.")
        return

    total_min = sum(s.get("total_minutes", 0) for s in data["stats"].values())
    total_sessions = sum(s.get("sessions_completed", 0) for s in data["stats"].values())
    total_users = len(data["stats"])
    h, m = divmod(total_min, 60)

    top_streak = max(
        ((uid, d["streaks"].get(uid, {}).get("streak", 0)) for uid in data["stats"]),
        key=lambda x: x[1], default=(None, 0)
    )
    streak_name = data["stats"].get(top_streak[0], {}).get("name", "N/A") if top_streak[0] else "N/A"

    await update.message.reply_text(
        f"📈 *إحصائيات المجموعة*\n\n"
        f"⏱ إجمالي وقت المذاكرة: *{h}h {m}m*\n"
        f"✅ سيشنات اكتملت: *{total_sessions}*\n"
        f"👥 عدد المذاكرين: *{total_users}*\n"
        f"🔥 أعلى streak: *{streak_name}* ({top_streak[1]} يوم)\n\n"
        f"_شوف الترتيب بـ /leaderboard_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /leaderboard ───────────────────────────────────────────────────────────
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not data["stats"]:
        await update.message.reply_text("📊 لسه مفيش بيانات! ابدأ سيشن أول.")
        return

    sorted_users = sorted(
        data["stats"].items(),
        key=lambda x: x[1].get("total_minutes", 0),
        reverse=True
    )[:10]

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = []
    for i, (uid, s) in enumerate(sorted_users):
        h, m = divmod(s.get("total_minutes", 0), 60)
        name = s.get("name") or s.get("username") or f"User {uid}"
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        streak_str = f" 🔥{streak}" if streak > 1 else ""
        badges = s.get("badges", [])
        top_badge = BADGES[badges[-1]][0] if badges else ""
        lines.append(f"{medals[i]} *{name}* — {h}h {m}m{streak_str} {top_badge}")

    await update.message.reply_text(
        f"🏆 *ليدربورد المذاكرة*\n\n" + "\n".join(lines) + "\n\n_بيتحدث في الوقت الفعلي_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /end ───────────────────────────────────────────────────────────────────
async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active", "paused"):
        await update.message.reply_text("❌ مفيش سيشن تطلع منها.")
        return

    if uid not in session["participants"]:
        await update.message.reply_text("❌ انت مش في السيشن دي.")
        return

    del session["participants"][uid]

    if session["state"] in ("active", "paused"):
        await unrestrict_user(context.bot, update.effective_chat.id, user.id)

    save_data(data)

    await update.message.reply_text(
        f"👋 *{user.full_name}* خرج من السيشن.\n"
        f"_فاكر: الاستمرارية هي المفتاح! شوفك المرة الجاية. 📚_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /reset ─────────────────────────────────────────────────────────────────
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
    if not is_admin(member):
        await update.message.reply_text("❌ الأمر ده للأدمن بس.")
        return

    session = get_session(chat_id)
    if not session:
        await update.message.reply_text("❌ مفيش سيشن عشان تمسحها.")
        return

    # Cancel all related jobs
    for prefix in [f"start_{chat_id}", f"end_{chat_id}", f"warn_{chat_id}"]:
        for job in context.job_queue.get_jobs_by_name(prefix):
            job.schedule_removal()

    data["sessions"].pop(chat_id, None)
    save_data(data)

    await unlock_chat(context.bot, update.effective_chat.id)
    await update.message.reply_text(
        "🗑 *السيشن اتمسحت!*\n\nالشات اتفتح. تقدر تبدأ سيشن جديدة بـ /study 🔄",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Guard messages ─────────────────────────────────────────────────────────
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type == "private":
        return

    chat_id = str(update.effective_chat.id)
    uid = str(update.effective_user.id)
    session = get_session(chat_id)

    if not session or session["state"] != "active":
        return
    if uid not in session["participants"]:
        return
    if uid in session.get("breaks", {}):
        return

    try:
        await update.message.delete()

        # سجّل المخالفة
        violation_count = add_violation(chat_id, uid)
        total_penalty_so_far = min(violation_count * POINTS_PENALTY_PER_MSG, POINTS_PENALTY_MAX)

        # اخصم النقاط — بس لو ما وصلناش للحد الأقصى
        if violation_count * POINTS_PENALTY_PER_MSG <= POINTS_PENALTY_MAX:
            deduct_points(uid, POINTS_PENALTY_PER_MSG, name=update.effective_user.full_name)
            save_data(data)

        mention = f"[{update.effective_user.first_name}](tg://user?id={uid})"
        reply = random.choice(GUARD_REPLIES).format(name=mention)

        current_pts = get_points(uid).get("total", 0)
        penalty_note = f"\n📉 *-{POINTS_PENALTY_PER_MSG} نقطة!* رصيدك: *{current_pts}* نقطة"

        # لو وصل الحد الأقصى
        if total_penalty_so_far >= POINTS_PENALTY_MAX:
            penalty_note += f"\n⚠️ وصلت أقصى خصم في السيشن دي ({POINTS_PENALTY_MAX} نقطة)"

        await context.bot.send_message(
            update.effective_chat.id,
            reply + penalty_note,
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError:
        pass

# ── /points ────────────────────────────────────────────────────────────────
async def cmd_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    p = get_points(uid)

    today_pts = p.get("today", 0)
    week_pts  = p.get("this_week", 0)
    total_pts = p.get("total", 0)

    # ترتيب اليوزر في كل فئة
    def rank_in(scope: str) -> int:
        board = build_points_board(scope, top_n=9999)
        for i, (bid, _, _) in enumerate(board, 1):
            if bid == uid:
                return i
        return 0

    rank_today = rank_in("today")
    rank_week  = rank_in("this_week")
    rank_total = rank_in("total")

    rank_str = lambda r: f"#{r}" if r else "—"

    await update.message.reply_text(
        f"🪙 *رصيد نقاطك*\n\n"
        f"📅 النهارده:  *{today_pts}* نقطة  (ترتيب: {rank_str(rank_today)})\n"
        f"📆 الأسبوع:  *{week_pts}* نقطة  (ترتيب: {rank_str(rank_week)})\n"
        f"🏆 الإجمالي: *{total_pts}* نقطة  (ترتيب: {rank_str(rank_total)})\n\n"
        f"*كيف بتكسب نقاط؟*\n"
        f"✅ إكمال سيشن: *+{POINTS_SESSION_COMPLETE}*\n"
        f"⏱ لكل ساعة: *+{POINTS_PER_HOUR}*\n"
        f"🔥 بونص streak (×أيام): *+{POINTS_STREAK_BONUS}*\n"
        f"✨ بدون مخالفات: *+{POINTS_NO_VIOLATION}*\n"
        f"❌ كل رسالة أثناء السيشن: *-{POINTS_PENALTY_PER_MSG}* (أقصى -{POINTS_PENALTY_MAX})",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /pointsboard ───────────────────────────────────────────────────────────
async def cmd_pointsboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    scope_arg = context.args[0].lower() if context.args else "today"
    scope_map = {
        "today": ("today", "🌅 أفضل 5 النهارده"),
        "اليوم": ("today", "🌅 أفضل 5 النهارده"),
        "week": ("this_week", "📆 أفضل 5 الأسبوع"),
        "الاسبوع": ("this_week", "📆 أفضل 5 الأسبوع"),
        "total": ("total", "🏆 أفضل 5 الكل"),
        "الكل": ("total", "🏆 أفضل 5 الكل"),
    }
    scope, title = scope_map.get(scope_arg, ("today", "🌅 أفضل 5 النهارده"))

    board = build_points_board(scope, top_n=5)
    if not board:
        await update.message.reply_text("😴 لسه مفيش نقاط في الفترة دي! ابدأ سيشن أول.")
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [f"{medals[i]} *{name}* — {pts} نقطة 🪙"
             for i, (_, name, pts) in enumerate(board)]

    scope_hint = {
        "today": "_/pointsboard week للأسبوع | /pointsboard total للكل_",
        "this_week": "_/pointsboard today لليوم | /pointsboard total للكل_",
        "total": "_/pointsboard today لليوم | /pointsboard week للأسبوع_",
    }[scope]

    await update.message.reply_text(
        f"{title}\n\n" + "\n".join(lines) + f"\n\n{scope_hint}",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Scheduled: Daily top 5 @ midnight ─────────────────────────────────────
async def daily_top5_job(context: ContextTypes.DEFAULT_TYPE):
    """بيتبعت تلقائياً كل يوم الساعة 11:59 بالليل لكل المجموعات النشيطة."""
    chat_int = context.job.data["chat_int"]
    board = build_points_board("today", top_n=5)
    if not board:
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [f"{medals[i]} *{name}* — {pts} نقطة 🪙"
             for i, (_, name, pts) in enumerate(board)]

    # صاحب المركز الأول يستاهل تشجيع خاص
    winner_mention = f"[{board[0][1]}](tg://user?id={board[0][0]})"

    try:
        await context.bot.send_message(
            chat_int,
            f"🌙 *ملخص اليوم — أفضل 5*\n\n"
            + "\n".join(lines) +
            f"\n\n🎉 تهانينا {winner_mention}! الأول النهارده 🏆\n"
            f"_النقاط اليومية بتتصفر بكره. روح نام تعبت! 😴_",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.warning(f"Daily top5 send failed: {e}")

# ── Scheduled: Weekly top 5 every Friday @ 11:59 PM ──────────────────────
async def weekly_top5_job(context: ContextTypes.DEFAULT_TYPE):
    """بيتبعت كل جمعة الساعة 11:59 بالليل."""
    chat_int = context.job.data["chat_int"]
    board = build_points_board("this_week", top_n=5)
    if not board:
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [f"{medals[i]} *{name}* — {pts} نقطة 🪙"
             for i, (_, name, pts) in enumerate(board)]

    winner_mention = f"[{board[0][1]}](tg://user?id={board[0][0]})"

    try:
        await context.bot.send_message(
            chat_int,
            f"📆 *ملخص الأسبوع — أفضل 5*\n\n"
            + "\n".join(lines) +
            f"\n\n👑 {winner_mention} بطل الأسبوع! يستاهل وقفة تصفيق! 👏\n"
            f"_الأسبوع الجديد بيبدأ بكره. خلوا الجد يرتفع! 🚀_",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.warning(f"Weekly top5 send failed: {e}")

def schedule_recurring_jobs(app, chat_int: int):
    """
    جدولة الـ jobs اليومية والأسبوعية لمجموعة معينة.
    بيتعمل مرة واحدة لما البوت يشوف المجموعة للأول.
    """
    # Daily @ 23:59 Cairo time — كل يوم
    now_dt = now()
    target_daily = now_dt.replace(hour=23, minute=59, second=0, microsecond=0)
    if target_daily <= now_dt:
        target_daily += timedelta(days=1)
    delay_daily = (target_daily - now_dt).total_seconds()

    job_name_d = f"daily_{chat_int}"
    if not app.job_queue.get_jobs_by_name(job_name_d):
        app.job_queue.run_repeating(
            daily_top5_job,
            interval=86400,      # كل 24 ساعة
            first=delay_daily,
            data={"chat_int": chat_int},
            name=job_name_d,
        )

    # Weekly Friday @ 23:59 Cairo time — الجمعة = weekday 4
    days_until_friday = (4 - now_dt.weekday()) % 7
    if days_until_friday == 0:
        # لو النهارده جمعة، بس الوقت فات الـ 23:59، استنى أسبوع
        target_today = now_dt.replace(hour=23, minute=59, second=0, microsecond=0)
        if target_today <= now_dt:
            days_until_friday = 7
    target_weekly = (now_dt + timedelta(days=days_until_friday)).replace(
        hour=23, minute=59, second=0, microsecond=0
    )
    delay_weekly = (target_weekly - now_dt).total_seconds()

    job_name_w = f"weekly_{chat_int}"
    if not app.job_queue.get_jobs_by_name(job_name_w):
        app.job_queue.run_repeating(
            weekly_top5_job,
            interval=604800,     # كل 7 أيام
            first=delay_weekly,
            data={"chat_int": chat_int},
            name=job_name_w,
        )

# ── Track new groups and schedule jobs ────────────────────────────────────
async def on_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بيتسجل كل مجموعة جديدة يشوفها البوت ويجدول الـ jobs."""
    if not update.effective_chat or update.effective_chat.type == "private":
        return
    chat_int = update.effective_chat.id
    schedule_recurring_jobs(context.application, chat_int)

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN مش متحدد. حطه في environment variables.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("study",       cmd_study))
    app.add_handler(CommandHandler("pomodoro",    cmd_pomodoro))
    app.add_handler(CommandHandler("schedule",    cmd_schedule))
    app.add_handler(CommandHandler("goal",        cmd_goal))
    app.add_handler(CommandHandler("pause",       cmd_pause))
    app.add_handler(CommandHandler("resume",      cmd_resume))
    app.add_handler(CommandHandler("break",       cmd_break))
    app.add_handler(CommandHandler("back",        cmd_back))
    app.add_handler(CommandHandler("kick",        cmd_kick))
    app.add_handler(CommandHandler("end",         cmd_end))
    app.add_handler(CommandHandler("reset",       cmd_reset))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("groupstats",  cmd_groupstats))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("points",      cmd_points))
    app.add_handler(CommandHandler("pointsboard", cmd_pointsboard))

    app.add_handler(CallbackQueryHandler(join_callback, pattern=r"^join_"))

    # Guard + group tracker (TEXT messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))
    # Track groups for scheduling (all message types)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, on_any_message), group=1)

    logger.info("🤖 StudyLock Bot شغال...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
