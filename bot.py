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

# ── Permissions helpers ────────────────────────────────────────────────────

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
    return {"sessions": {}, "stats": {}, "streaks": {}}

def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, default=str))

data = load_data()

# ── Motivational messages ──────────────────────────────────────────────────

MOTIVATIONAL = [
    "⚡ Keep going — every minute counts!",
    "📖 You're building your future right now. Don't stop.",
    "🔥 Consistency beats intensity. Stay locked in!",
    "🧠 Your brain is growing stronger with every page.",
    "💪 Champions are made in moments like this.",
    "🌟 One hour closer to your goal. You've got this!",
    "🎯 Focus. Breathe. Keep going.",
    "🚀 The effort you put in today will pay off tomorrow.",
]

BREAK_OVER = [
    "☕ Break's over! Time to get back to it. 📚",
    "⏰ Rest time's up — back to work, champion! 💪",
    "🔔 Break ended! Your future self will thank you. 🚀",
    "📚 Recharge done — now let's lock back in! 🔒",
]

# ── Points system ──────────────────────────────────────────────────────────
# نقاط لكل دقيقة مذاكرة
POINTS_PER_MINUTE = 1
# خصم نقاط لو بعت رسالة وهو في سيشن
POINTS_PENALTY = 5

# ── Utility ────────────────────────────────────────────────────────────────

def now() -> datetime:
    return datetime.now(TZ)

def fmt_time(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
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
    """Parse '2h', '90m', '1h30m' → minutes"""
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
        "username": "", "name": "",
        "points": 0,
        "weekly_points": 0,
        "weekly_minutes": 0,
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

def add_points(user_id: str, points: int):
    stats = get_stats(user_id)
    stats["points"] = max(0, stats.get("points", 0) + points)
    stats["weekly_points"] = max(0, stats.get("weekly_points", 0) + points)

def build_leaderboard_text(title: str, sort_key: str = "points") -> str:
    if not data["stats"]:
        return "📊 لا يوجد بيانات بعد! ابدأ سيشن مذاكرة."

    sorted_users = sorted(
        data["stats"].items(),
        key=lambda x: x[1].get(sort_key, 0),
        reverse=True
    )[:10]

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = []
    for i, (uid, s) in enumerate(sorted_users):
        pts = s.get(sort_key, 0)
        name = s.get("name") or s.get("username") or f"User {uid}"
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        streak_str = f" 🔥{streak}" if streak > 1 else ""
        if sort_key == "weekly_points":
            h, m = divmod(s.get("weekly_minutes", 0), 60)
            time_str = f" ({h}h {m}m)"
        else:
            h, m = divmod(s.get("total_minutes", 0), 60)
            time_str = f" ({h}h {m}m)"
        lines.append(f"{medals[i]} *{name}* — {pts} نقطة{time_str}{streak_str}")

    return f"🏆 *{title}*\n\n" + "\n".join(lines) + "\n\n_محدّث تلقائياً_"

# ── Pin/Unpin session message ──────────────────────────────────────────────

async def pin_message(bot, chat_id: int, message_id: int):
    try:
        await bot.pin_chat_message(chat_id, message_id, disable_notification=True)
    except TelegramError as e:
        logger.warning(f"Pin failed: {e}")

async def unpin_message(bot, chat_id: int, message_id: int):
    try:
        await bot.unpin_chat_message(chat_id, message_id)
    except TelegramError as e:
        logger.warning(f"Unpin failed: {e}")

# ── /study ─────────────────────────────────────────────────────────────────

async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في المجموعات بس.")
        return

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active"):
        # السماح بالانضمام لسيشن قائمة
        uid = str(user.id)
        if uid in session["participants"]:
            await update.message.reply_text("⚠️ في سيشن شغالة! انت شارك فيها بالفعل.\nاستخدم /status لمتابعتها.")
        else:
            session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
            stats = get_stats(uid)
            stats["username"] = user.username or ""
            stats["name"] = user.full_name
            stats["sessions_joined"] += 1
            save_data(data)
            await update.message.reply_text(
                f"✅ *{user.full_name}* انضم للسيشن الشغالة!\n"
                f"⏱ المدة: *{fmt_duration(session['duration'])}*",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

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
            "❌ مدة غلط. استخدم صيغ زي `1h`، `90m`، `1h30m`.\n"
            "الحد الأدنى: 10 دقائق | الحد الأقصى: 8 ساعات.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    data["sessions"][chat_id] = {
        "state": "waiting",
        "duration": duration,
        "started_by": user.id,
        "participants": {str(user.id): {"name": user.full_name, "username": user.username or ""}},
        "start_time": None,
        "end_time": None,
        "breaks": {},
        "pomodoro": False,
        "pomo_cycle": 0,
        "pinned_message_id": None,
    }
    save_data(data)

    stats = get_stats(str(user.id))
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    msg = await update.message.reply_text(
        f"📚 *سيشن مذاكرة جديدة!*\n\n"
        f"👤 بدأها: {user.full_name}\n"
        f"⏱ المدة: *{fmt_duration(duration)}*\n"
        f"👥 المشاركين: 1\n\n"
        f"🚀 *السيشن هتبدأ فوراً!*\n"
        f"اضغط الزرار للانضمام 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # حفظ id الرسالة للـ pin
    data["sessions"][chat_id]["pinned_message_id"] = msg.message_id
    save_data(data)

    # Pin الرسالة
    await pin_message(context.bot, update.effective_chat.id, msg.message_id)

    # السيشن تبدأ فوراً (بعد ثانيتين علشان الناس تشوف الرسالة)
    context.job_queue.run_once(
        start_session_job,
        when=2,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}",
    )

# ── Join button callback ───────────────────────────────────────────────────

async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    _, chat_id = query.data.split("_", 1)

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active"):
        await query.answer("❌ السيشن دي اتقفلت أو انتهت.", show_alert=True)
        return

    uid = str(user.id)
    if uid in session["participants"]:
        await query.answer("✅ انت شارك أصلاً!", show_alert=True)
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
    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    try:
        await query.edit_message_text(
            f"📚 *سيشن المذاكرة — {state_text}*\n\n"
            f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
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
    chat_int = job_data["chat_int"]

    session = get_session(chat_id)
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

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]

    sent = await context.bot.send_message(
        chat_int,
        f"🔒 *السيشن بدأت!*\n\n"
        f"👥 المشاركين: {', '.join(names)}\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"🏁 تنتهي الساعة: *{fmt_time(end.isoformat())}*\n\n"
        f"📵 *الرسايل هتتمسح أثناء السيشن* — ركز! 🎯\n"
        f"ممكن تنضم لسه 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # Pin رسالة السيشن الشغالة
    await pin_message(context.bot, chat_int, sent.message_id)
    # تحديث الـ pinned message id
    session["active_pinned_message_id"] = sent.message_id
    save_data(data)

    # رسايل تحفيز كل ساعة
    for i in range(1, session["duration"] // 60 + 1):
        msg = MOTIVATIONAL[i % len(MOTIVATIONAL)]
        context.job_queue.run_once(
            send_motivation_job,
            when=i * 3600,
            data={"chat_int": chat_int, "msg": msg, "elapsed": fmt_duration(i * 60), "total": session["duration"]},
            name=f"motiv_{chat_id}_{i}",
        )

    # جدولة نهاية السيشن
    context.job_queue.run_once(
        end_session_job,
        when=session["duration"] * 60,
        data={"chat_id": chat_id, "chat_int": chat_int},
        name=f"end_{chat_id}",
    )

# ── Motivational job ───────────────────────────────────────────────────────

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
    chat_int = job_data["chat_int"]

    session = get_session(chat_id)
    if not session:
        return

    # تحديث إحصائيات ونقاط المشاركين
    for uid, pdata_u in session["participants"].items():
        stats = get_stats(uid)
        stats["total_minutes"] += session["duration"]
        stats["weekly_minutes"] = stats.get("weekly_minutes", 0) + session["duration"]
        stats["sessions_completed"] += 1
        stats["last_study_date"] = now().date().isoformat()
        # إضافة نقاط
        earned = session["duration"] * POINTS_PER_MINUTE
        add_points(uid, earned)
        update_streak(uid)

    session["state"] = "ended"
    save_data(data)

    # Unpin رسالة السيشن
    active_pin = session.get("active_pinned_message_id")
    if active_pin:
        await unpin_message(context.bot, chat_int, active_pin)

    # ملخص النتائج
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

    await context.bot.send_message(
        chat_int,
        f"✅ *السيشن انتهت!*\n\n"
        f"🎉 عظيم — كلكم عملتوا حاجة كويسة النهارده!\n"
        f"⏱ وقت المذاكرة: *{fmt_duration(session['duration'])}*\n\n"
        f"*🏆 الترتيب:*\n" + "\n".join(lines) + "\n\n"
        f"💤 استريح — استخدم /break <دقايق> لتايمر استراحة.\n"
        f"📊 /stats لإحصائياتك | /leaderboard للترتيب الكامل",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Daily leaderboard job ──────────────────────────────────────────────────

async def send_daily_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    """يتبعت كل يوم الساعة 12 صباحاً بتوقيت القاهرة لكل الجروبات"""
    for chat_id_str, session in data["sessions"].items():
        # بعت للجروبات اللي عندها سيشنات سابقة
        try:
            chat_int = int(chat_id_str)
            text = build_leaderboard_text("🏆 ترتيب اليوم — النقاط الكلية", sort_key="points")
            await context.bot.send_message(chat_int, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"Daily leaderboard failed for {chat_id_str}: {e}")

# ── Weekly leaderboard job ─────────────────────────────────────────────────

async def send_weekly_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    """يتبعت كل أسبوع ويصفر النقاط الأسبوعية"""
    for chat_id_str in data["sessions"]:
        try:
            chat_int = int(chat_id_str)
            text = build_leaderboard_text("🏆 ترتيب الأسبوع — النقاط الأسبوعية", sort_key="weekly_points")
            await context.bot.send_message(chat_int, text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"Weekly leaderboard failed for {chat_id_str}: {e}")

    # تصفير النقاط الأسبوعية
    for uid in data["stats"]:
        data["stats"][uid]["weekly_points"] = 0
        data["stats"][uid]["weekly_minutes"] = 0
    save_data(data)

# ── Schedule daily/weekly jobs ─────────────────────────────────────────────

def schedule_recurring_jobs(app):
    """جدولة الترتيب اليومي والأسبوعي"""
    job_queue = app.job_queue

    # حساب الوقت حتى الساعة 12 صباحاً القاهرة
    cairo_now = now()
    midnight = cairo_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    seconds_until_midnight = (midnight - cairo_now).total_seconds()

    # يومي الساعة 12 صباحاً
    job_queue.run_repeating(
        send_daily_leaderboard,
        interval=86400,  # 24 ساعة
        first=seconds_until_midnight,
        name="daily_leaderboard",
    )

    # حساب الوقت حتى الإثنين الجاي الساعة 12 صباحاً
    days_until_monday = (7 - cairo_now.weekday()) % 7 or 7
    next_monday = cairo_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
    seconds_until_monday = (next_monday - cairo_now).total_seconds()

    # أسبوعي كل إثنين الساعة 12 صباحاً
    job_queue.run_repeating(
        send_weekly_leaderboard,
        interval=604800,  # 7 أيام
        first=seconds_until_monday,
        name="weekly_leaderboard",
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
        await update.message.reply_text("❌ مفيش سيشن. ابدأ واحدة بـ /study.")
        return

    if uid not in session["participants"]:
        await update.message.reply_text("❌ انت مش مشارك في السيشن دي.")
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
        if not 1 <= minutes <= 60:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ المدة لازم تكون بين 1 و 60 دقيقة.")
        return

    break_end = now() + timedelta(minutes=minutes)
    session["breaks"][uid] = {
        "end": break_end.isoformat(),
        "duration": minutes,
        "name": user.full_name,
    }
    save_data(data)

    await update.message.reply_text(
        f"☕ *استراحة لـ {user.full_name}*\n\n"
        f"⏱ المدة: *{minutes} دقيقة*\n"
        f"🔔 هترجع الساعة: *{fmt_time(break_end.isoformat())}*\n\n"
        f"_استرح — رسايلك مش هتتمسح للحظة!_ 😴",
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

    msg = random.choice(BREAK_OVER)
    mention = f"[{name}](tg://user?id={uid})"
    await context.bot.send_message(
        chat_int,
        f"{msg}\n\n{mention}, استراحتك خلصت — ارجع تذاكر! 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /back (end break early) ────────────────────────────────────────────────

async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    session = get_session(chat_id)
    if not session or uid not in session.get("breaks", {}):
        await update.message.reply_text("❌ مفيش استراحة شغالة ليك.")
        return

    jobs = context.job_queue.get_jobs_by_name(f"break_{chat_id}_{uid}")
    for job in jobs:
        job.schedule_removal()

    del session["breaks"][uid]
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

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active"):
        # السماح بالانضمام
        uid = str(user.id)
        if uid not in session["participants"]:
            session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
            stats = get_stats(uid)
            stats["sessions_joined"] += 1
            save_data(data)
            await update.message.reply_text(f"✅ *{user.full_name}* انضم للسيشن!", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("⚠️ في سيشن شغالة وانت فيها بالفعل.")
        return

    args = context.args
    cycles = int(args[0]) if args and args[0].isdigit() else 4
    cycles = max(1, min(cycles, 8))
    work_min = 25
    break_min = 5
    total = cycles * (work_min + break_min)

    data["sessions"][chat_id] = {
        "state": "waiting",
        "duration": total,
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
    }
    stats = get_stats(str(user.id))
    stats["sessions_joined"] += 1
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ انضم للبومودورو", callback_data=f"join_{chat_id}")]]
    msg = await update.message.reply_text(
        f"🍅 *سيشن بومودورو!*\n\n"
        f"👤 بدأها: {user.full_name}\n"
        f"🔄 الدورات: *{cycles}* × (25 دقيقة مذاكرة + 5 استراحة)\n"
        f"⏱ المجموع: *{fmt_duration(total)}*\n\n"
        f"🚀 هتبدأ فوراً!\nاضغط للانضمام 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    data["sessions"][chat_id]["pinned_message_id"] = msg.message_id
    save_data(data)
    await pin_message(context.bot, update.effective_chat.id, msg.message_id)

    context.job_queue.run_once(
        start_session_job,
        when=2,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}",
    )

# ── /status ────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    session = get_session(chat_id)

    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text(
            "💤 *مفيش سيشن شغالة.*\n\nابدأ واحدة بـ `/study <المدة>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

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

    msg = (
        f"{state_emoji} *حالة السيشن: {state_text}*\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
    )
    if session.get("end_time"):
        msg += f"🏁 تنتهي الساعة: *{fmt_time(session['end_time'])}*\n"
    msg += f"\n*المشاركين ({len(lines)}):*\n" + "\n".join(lines)

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ── /stats ─────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    stats = get_stats(uid)
    streak = data["streaks"].get(uid, {}).get("streak", 0)
    hours = stats["total_minutes"] // 60
    minutes = stats["total_minutes"] % 60

    await update.message.reply_text(
        f"📊 *إحصائياتك*\n\n"
        f"⏱ وقت المذاكرة الكلي: *{hours}h {minutes}m*\n"
        f"✅ سيشنات اكتملت: *{stats['sessions_completed']}*\n"
        f"👥 سيشنات انضممت ليها: *{stats['sessions_joined']}*\n"
        f"🔥 سلسلة الأيام: *{streak} يوم*\n"
        f"⭐ نقاطك الكلية: *{stats.get('points', 0)}*\n"
        f"📅 آخر يوم مذاكرة: *{stats.get('last_study_date') or 'N/A'}*",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /leaderboard ───────────────────────────────────────────────────────────

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = build_leaderboard_text("🏆 ترتيب النقاط الكلية", sort_key="points")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = build_leaderboard_text("🏆 ترتيب الأسبوع", sort_key="weekly_points")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ── /end (leave session) ───────────────────────────────────────────────────

async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ مفيش سيشن شغالة تسيبها.")
        return

    if uid not in session["participants"]:
        await update.message.reply_text("❌ انت مش في السيشن دي.")
        return

    del session["participants"][uid]
    save_data(data)

    await update.message.reply_text(
        f"👋 *{user.full_name}* ساب السيشن.\n"
        f"_المداومة هي المفتاح! شوفك المرة الجاية._ 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Delete messages during session (no restrict) ───────────────────────────

async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مسح رسايل المشاركين أثناء السيشن + خصم نقاط"""
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
        return  # في استراحة، مسموح يبعت

    try:
        await update.message.delete()
        # خصم نقاط
        add_points(uid, -POINTS_PENALTY)
        save_data(data)

        mention = f"[{update.effective_user.first_name}](tg://user?id={uid})"
        stats = get_stats(uid)
        await context.bot.send_message(
            update.effective_chat.id,
            f"📵 {mention} ركّز يسطا! 🔒\n"
            f"_اتخصم منك {POINTS_PENALTY} نقاط — نقاطك: {stats.get('points', 0)}_",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError:
        pass

# ── /start ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *StudyLock Bot*\n\n"
        "بوت لتنظيم جلسات المذاكرة في المجموعات.\n\n"
        "*الأوامر:*\n"
        "/study 1h — ابدأ سيشن ساعة\n"
        "/pomodoro — سيشن بومودورو\n"
        "/break 10 — استراحة 10 دقايق\n"
        "/back — خلّص استراحتك بدري\n"
        "/status — حالة السيشن الحالية\n"
        "/stats — إحصائياتك\n"
        "/leaderboard — الترتيب الكلي\n"
        "/weekly — ترتيب الأسبوع\n"
        "/end — اخرج من السيشن\n\n"
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
    app.add_handler(CallbackQueryHandler(join_callback, pattern=r"^join_"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, guard_messages))

    # جدولة الإرسال التلقائي للترتيب
    async def post_init(application):
        schedule_recurring_jobs(application)

    app.post_init = post_init

    logger.info("StudyLock Bot شغال...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
