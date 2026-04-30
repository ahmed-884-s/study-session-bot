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

# ── Use persistent storage (not /tmp which gets wiped on restart) ──────────
DATA_FILE = Path(os.getenv("DATA_PATH", "/data/studybot_data.json"))
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

TZ = ZoneInfo("Africa/Cairo")

# ── Permissions helpers ────────────────────────────────────────────────────
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
    return {"sessions": {}, "stats": {}, "streaks": {}}

def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, default=str, ensure_ascii=False))

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
    "📚 You showed up today — that's already a win.",
    "🏆 Every expert was once a beginner. Keep going!",
]

BREAK_OVER = [
    "☕ Break's over! Time to get back to it. 📚",
    "⏰ Rest time's up — back to work, champion! 💪",
    "🔔 Break ended! Your future self will thank you. 🚀",
    "📚 Recharge done — now let's lock back in! 🔒",
]

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
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h {m}m"
    elif h:
        return f"{h}h"
    return f"{m}m"

def parse_duration(text: str) -> int | None:
    """Parse '2h', '90m', '1h30m' → minutes"""
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
    })

def update_streak(user_id: str):
    """Only call this when a session is actually completed."""
    s = data["streaks"].setdefault(user_id, {"streak": 0, "last_date": None})
    today = now().date().isoformat()
    if s["last_date"] == today:
        return  # already counted today
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    if s["last_date"] == yesterday:
        s["streak"] += 1
    else:
        s["streak"] = 1
    s["last_date"] = today

def is_admin(member) -> bool:
    return member.status in ("administrator", "creator")

# ── Lock/Unlock chat ───────────────────────────────────────────────────────
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
        logger.warning(f"Restrict user failed: {e}")

async def unrestrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, UNLOCKED)
    except TelegramError as e:
        logger.warning(f"Unrestrict user failed: {e}")

# ── /start ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *StudyLock Bot*\n\n"
        "I help study groups stay focused by locking the chat during sessions.\n\n"
        "*Commands:*\n"
        "`/study 2h` — Start a 2-hour session\n"
        "`/study 90m` — Start a 90-minute session\n"
        "`/pomodoro` — Start a Pomodoro session (4×25m)\n"
        "`/pomodoro 6` — Pomodoro with 6 cycles\n"
        "`/break 10` — Take a 10-minute break\n"
        "`/back` — End your break early\n"
        "`/status` — Check current session\n"
        "`/stats` — Your personal stats\n"
        "`/leaderboard` — Group rankings\n"
        "`/end` — Leave current session\n"
        "`/cancel` — Cancel session _(admins only)_\n\n"
        "_Add me to your group and make me admin to get started!_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /study ─────────────────────────────────────────────────────────────────
async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ This command only works in groups.")
        return

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active"):
        await update.message.reply_text("⚠️ A study session is already running!\nUse /status to check it.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "📚 *Usage:* `/study <duration>`\n\n"
            "Examples:\n`/study 2h` — 2 hours\n`/study 90m` — 90 minutes\n`/study 1h30m` — 1.5 hours",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    duration = parse_duration(args[0])
    if not duration:
        await update.message.reply_text(
            "❌ Invalid duration. Use formats like `2h`, `90m`, `1h30m`.\n"
            "Minimum: 10 minutes | Maximum: 8 hours.",
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
    }
    save_data(data)

    stats = get_stats(str(user.id))
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ Join Session", callback_data=f"join_{chat_id}")]]
    await update.message.reply_text(
        f"📚 *New Study Session!*\n\n"
        f"👤 Started by: {user.full_name}\n"
        f"⏱ Duration: *{fmt_duration(duration)}*\n"
        f"👥 Participants so far: 1\n\n"
        f"⏳ *You have 5 minutes to join!*\n"
        f"Session starts at: *{fmt_time(join_deadline)}*\n\n"
        f"Press the button below to join 👇",
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

    session = get_session(chat_id)
    if not session or session["state"] != "waiting":
        await query.answer("❌ This session is no longer open.", show_alert=True)
        return

    uid = str(user.id)
    if uid in session["participants"]:
        await query.answer("✅ You're already in!", show_alert=True)
        return

    session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
    stats = get_stats(uid)
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    names = [p["name"] for p in session["participants"].values()]
    await query.answer("✅ You joined the session!", show_alert=True)

    keyboard = [[InlineKeyboardButton("✋ Join Session", callback_data=f"join_{chat_id}")]]
    try:
        await query.edit_message_text(
            f"📚 *Study Session — Waiting Room*\n\n"
            f"⏱ Duration: *{fmt_duration(session['duration'])}*\n"
            f"👥 Participants ({len(names)}): {', '.join(names)}\n\n"
            f"⏳ Session starts in less than 5 minutes!\n"
            f"Press the button to join 👇",
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

    # If no one else joined, still proceed with the starter
    if not session["participants"]:
        session["state"] = "ended"
        save_data(data)
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
    await context.bot.send_message(
        chat_int,
        f"🔒 *Session Started!*\n\n"
        f"👥 Participants: {', '.join(names)}\n"
        f"⏱ Duration: *{fmt_duration(session['duration'])}*\n"
        f"🏁 Ends at: *{fmt_time(end.isoformat())}*\n\n"
        f"📵 *Chat is now locked.* Focus mode ON! 🎯",
        parse_mode=ParseMode.MARKDOWN,
    )

    duration = session["duration"]

    # Schedule motivational messages every 30 minutes (not just every hour)
    interval = 30  # minutes
    steps = duration // interval
    for i in range(1, steps + 1):
        elapsed_min = i * interval
        if elapsed_min >= duration:
            break
        msg = MOTIVATIONAL[i % len(MOTIVATIONAL)]
        context.job_queue.run_once(
            send_motivation_job,
            when=elapsed_min * 60,
            data={
                "chat_int": chat_int,
                "msg": msg,
                "elapsed": fmt_duration(elapsed_min),
                "remaining": fmt_duration(duration - elapsed_min),
            },
            name=f"motiv_{chat_id}_{i}",
        )

    # Schedule session end
    context.job_queue.run_once(
        end_session_job,
        when=duration * 60,
        data={"chat_id": chat_id, "chat_int": chat_int},
        name=f"end_{chat_id}",
    )

# ── Motivational job ───────────────────────────────────────────────────────
async def send_motivation_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await context.bot.send_message(
        d["chat_int"],
        f"{d['msg']}\n\n"
        f"⏱ *{d['elapsed']}* elapsed · *{d['remaining']}* remaining",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── End session job ────────────────────────────────────────────────────────
async def end_session_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    chat_int = job_data["chat_int"]

    session = get_session(chat_id)
    if not session or session["state"] != "active":
        return

    # Update stats for all participants
    for uid in session["participants"]:
        stats = get_stats(uid)
        stats["total_minutes"] += session["duration"]
        stats["sessions_completed"] += 1
        stats["last_study_date"] = now().date().isoformat()
        update_streak(uid)  # streak only updates on completion

    session["state"] = "ended"
    save_data(data)

    await unlock_chat(context.bot, chat_int)

    # Build leaderboard-style summary
    lines = []
    for uid, pinfo in session["participants"].items():
        stats = get_stats(uid)
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        streak_str = f" 🔥 {streak} day streak" if streak > 1 else ""
        lines.append(f"  • {pinfo['name']} — {fmt_duration(session['duration'])}{streak_str}")

    await context.bot.send_message(
        chat_int,
        f"✅ *Session Complete!*\n\n"
        f"🎉 You all crushed it!\n"
        f"⏱ Total study time: *{fmt_duration(session['duration'])}*\n\n"
        f"*Participants:*\n" + "\n".join(lines) + "\n\n"
        f"💤 Chat is *unlocked*. You deserve a break!\n"
        f"_Use /break <minutes> to start a personal break timer._\n"
        f"_Use /stats to see your study history._",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /break ─────────────────────────────────────────────────────────────────
async def cmd_break(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ This command only works in groups.")
        return

    session = get_session(chat_id)
    if not session or session["state"] not in ("ended", "active"):
        await update.message.reply_text("❌ No recent session found. Start one with /study.")
        return

    if uid not in session["participants"]:
        await update.message.reply_text("❌ You weren't part of this session.")
        return

    if uid in session.get("breaks", {}):
        await update.message.reply_text("⚠️ You're already on a break! Use /back to end it early.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "☕ *Usage:* `/break <minutes>`\n\nExamples: `/break 10` or `/break 15`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        minutes = int(args[0])
        if not 1 <= minutes <= 60:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Break duration must be between 1 and 60 minutes.")
        return

    break_end = now() + timedelta(minutes=minutes)
    session.setdefault("breaks", {})[uid] = {
        "end": break_end.isoformat(),
        "duration": minutes,
        "name": user.full_name,
    }
    save_data(data)

    await restrict_user(context.bot, update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"☕ *Break started for {user.full_name}*\n\n"
        f"⏱ Duration: *{minutes} minutes*\n"
        f"🔔 You'll be reminded at: *{fmt_time(break_end.isoformat())}*\n\n"
        f"_Relax and recharge!_ 😴",
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

    msg = random.choice(BREAK_OVER)
    mention = f"[{name}](tg://user?id={uid})"
    await context.bot.send_message(
        chat_int,
        f"{msg}\n\n{mention}, break's over — back to studying! 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Re-lock if session is still active
    session = get_session(chat_id)
    if session and session.get("state") == "active":
        await restrict_user(context.bot, chat_int, int(uid))
        await context.bot.send_message(
            chat_int,
            f"🔒 {mention} is back in focus mode!",
            parse_mode=ParseMode.MARKDOWN,
        )

# ── /back (end break early) ────────────────────────────────────────────────
async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    session = get_session(chat_id)
    if not session or uid not in session.get("breaks", {}):
        await update.message.reply_text("❌ You don't have an active break.")
        return

    # Cancel scheduled job
    jobs = context.job_queue.get_jobs_by_name(f"break_{chat_id}_{uid}")
    for job in jobs:
        job.schedule_removal()

    del session["breaks"][uid]
    save_data(data)

    await unrestrict_user(context.bot, update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"💪 *{user.full_name}* ended their break early!\n"
        f"Back to grinding 📚🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Re-lock if session is still active
    if session.get("state") == "active":
        await restrict_user(context.bot, update.effective_chat.id, user.id)

# ── /pomodoro ──────────────────────────────────────────────────────────────
async def cmd_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ Groups only.")
        return

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active"):
        await update.message.reply_text("⚠️ A session is already running. Use /status.")
        return

    args = context.args
    cycles = int(args[0]) if args and args[0].isdigit() else 4
    cycles = max(1, min(cycles, 8))

    work_min = 25
    break_min = 5
    # Total = all work time + break time (no break after last cycle)
    total = cycles * work_min + (cycles - 1) * break_min

    join_deadline = (now() + timedelta(minutes=5)).isoformat()
    data["sessions"][chat_id] = {
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

    stats = get_stats(str(user.id))
    stats["sessions_joined"] += 1
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ Join Pomodoro", callback_data=f"join_{chat_id}")]]
    await update.message.reply_text(
        f"🍅 *Pomodoro Session!*\n\n"
        f"👤 Started by: {user.full_name}\n"
        f"🔄 Cycles: *{cycles}* × (25m work + 5m break)\n"
        f"⏱ Total focus time: *{fmt_duration(cycles * work_min)}*\n"
        f"⏱ Total time: *{fmt_duration(total)}*\n\n"
        f"⏳ *5 minutes to join!*\n"
        f"Press the button below 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.job_queue.run_once(
        start_pomodoro_job,
        when=300,
        data={"chat_id": chat_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}",
    )

# ── Start Pomodoro job (proper cycle implementation) ───────────────────────
async def start_pomodoro_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    chat_int = job_data["chat_int"]

    session = get_session(chat_id)
    if not session or session["state"] != "waiting":
        return

    if not session["participants"]:
        session["state"] = "ended"
        save_data(data)
        return

    cycles = session["pomo_cycles"]
    work_min = session["pomo_work"]
    break_min = session["pomo_break"]
    total = session["duration"]

    start = now()
    end = start + timedelta(minutes=total)
    session["state"] = "active"
    session["start_time"] = start.isoformat()
    session["end_time"] = end.isoformat()
    save_data(data)

    names = [p["name"] for p in session["participants"].values()]
    await lock_chat(context.bot, chat_int)
    await context.bot.send_message(
        chat_int,
        f"🍅 *Pomodoro Started!*\n\n"
        f"👥 Participants: {', '.join(names)}\n"
        f"🔄 {cycles} cycles of 25m work + 5m break\n"
        f"🏁 Ends at: *{fmt_time(end.isoformat())}*\n\n"
        f"🔒 Chat locked — *Cycle 1/{cycles} starting now!* 💪",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Schedule each cycle transition
    elapsed = 0
    for cycle in range(1, cycles + 1):
        elapsed += work_min
        # Work period ends
        if cycle < cycles:
            # Schedule break start
            context.job_queue.run_once(
                pomo_break_start_job,
                when=elapsed * 60,
                data={
                    "chat_id": chat_id, "chat_int": chat_int,
                    "cycle": cycle, "cycles": cycles,
                    "break_min": break_min,
                },
                name=f"pomo_break_{chat_id}_{cycle}",
            )
            elapsed += break_min
            # Schedule next work period
            context.job_queue.run_once(
                pomo_work_start_job,
                when=elapsed * 60,
                data={
                    "chat_id": chat_id, "chat_int": chat_int,
                    "cycle": cycle + 1, "cycles": cycles,
                },
                name=f"pomo_work_{chat_id}_{cycle+1}",
            )

    # Schedule final end
    context.job_queue.run_once(
        end_session_job,
        when=total * 60,
        data={"chat_id": chat_id, "chat_int": chat_int},
        name=f"end_{chat_id}",
    )

async def pomo_break_start_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    session = get_session(d["chat_id"])
    if not session or session["state"] != "active":
        return
    await unlock_chat(context.bot, d["chat_int"])
    await context.bot.send_message(
        d["chat_int"],
        f"☕ *Cycle {d['cycle']}/{d['cycles']} complete!*\n\n"
        f"🎉 Nice work! Take a *{d['break_min']}-minute break*.\n"
        f"Chat is *unlocked* — stretch, breathe, grab some water! 💧",
        parse_mode=ParseMode.MARKDOWN,
    )

async def pomo_work_start_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    session = get_session(d["chat_id"])
    if not session or session["state"] != "active":
        return
    await lock_chat(context.bot, d["chat_int"])
    await context.bot.send_message(
        d["chat_int"],
        f"🍅 *Cycle {d['cycle']}/{d['cycles']} — Go!*\n\n"
        f"🔒 Chat locked again. Focus for 25 minutes! 💪",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /status ────────────────────────────────────────────────────────────────
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    session = get_session(chat_id)

    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text(
            "💤 *No active session right now.*\n\nStart one with `/study <duration>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    participants = session["participants"]
    breaks = session.get("breaks", {})

    lines = []
    for uid, pinfo in participants.items():
        if uid in breaks:
            end_str = fmt_time(breaks[uid]["end"])
            lines.append(f"  ☕ {pinfo['name']} — on break (back at {end_str})")
        else:
            lines.append(f"  📖 {pinfo['name']} — studying")

    state_emoji = "⏳" if session["state"] == "waiting" else "🔒"
    state_text = "Waiting for participants" if session["state"] == "waiting" else "Active — Chat Locked"

    pomo_info = ""
    if session.get("pomodoro"):
        cycle = session.get("pomo_cycle", 0)
        cycles = session.get("pomo_cycles", 4)
        pomo_info = f"🍅 Pomodoro: Cycle {cycle}/{cycles}\n"

    msg = (
        f"{state_emoji} *Session Status: {state_text}*\n\n"
        f"{pomo_info}"
        f"⏱ Duration: *{fmt_duration(session['duration'])}*\n"
    )
    if session.get("end_time"):
        msg += f"🏁 Ends at: *{fmt_time(session['end_time'])}*\n"

    msg += f"\n*Participants ({len(lines)}):*\n" + "\n".join(lines)

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ── /stats ─────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    stats = get_stats(uid)
    streak = data["streaks"].get(uid, {}).get("streak", 0)

    hours = stats["total_minutes"] // 60
    minutes = stats["total_minutes"] % 60

    # Compute rank
    sorted_users = sorted(
        data["stats"].items(),
        key=lambda x: x[1].get("total_minutes", 0),
        reverse=True
    )
    rank = next((i + 1 for i, (u, _) in enumerate(sorted_users) if u == uid), None)
    rank_str = f"🏅 Global rank: *#{rank}*\n" if rank else ""

    await update.message.reply_text(
        f"📊 *Your Study Stats*\n\n"
        f"⏱ Total study time: *{hours}h {minutes}m*\n"
        f"✅ Sessions completed: *{stats['sessions_completed']}*\n"
        f"👥 Sessions joined: *{stats['sessions_joined']}*\n"
        f"🔥 Current streak: *{streak} day{'s' if streak != 1 else ''}*\n"
        f"📅 Last study date: *{stats.get('last_study_date') or 'N/A'}*\n"
        f"{rank_str}",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /leaderboard ───────────────────────────────────────────────────────────
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not data["stats"]:
        await update.message.reply_text("📊 No data yet! Start a study session to begin tracking.")
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
        completed = s.get("sessions_completed", 0)
        lines.append(f"{medals[i]} *{name}* — {h}h {m}m · {completed} sessions{streak_str}")

    await update.message.reply_text(
        f"🏆 *Study Leaderboard — Top {len(lines)}*\n\n" + "\n".join(lines) + "\n\n_Updated in real-time_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /end (leave session) ───────────────────────────────────────────────────
async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ No active session to leave.")
        return

    if uid not in session["participants"]:
        await update.message.reply_text("❌ You're not part of this session.")
        return

    del session["participants"][uid]

    if session["state"] == "active":
        await unrestrict_user(context.bot, update.effective_chat.id, user.id)

    # If everyone left during an active session, end it
    if session["state"] == "active" and not session["participants"]:
        session["state"] = "ended"
        save_data(data)
        # Cancel end job
        for job in context.job_queue.get_jobs_by_name(f"end_{chat_id}"):
            job.schedule_removal()
        await unlock_chat(context.bot, update.effective_chat.id)
        await update.message.reply_text(
            "👋 Everyone left — session ended.\n_Start a new one anytime with /study_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    save_data(data)
    await update.message.reply_text(
        f"👋 *{user.full_name}* left the session.\n"
        f"_Remember: consistency is key! See you next time._ 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /cancel (admin only) ───────────────────────────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ Groups only.")
        return

    # Check admin
    member = await update.effective_chat.get_member(user.id)
    if not is_admin(member):
        await update.message.reply_text("❌ Only admins can cancel a session.")
        return

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ No active session to cancel.")
        return

    # Cancel all scheduled jobs for this chat
    for job_name in [f"start_{chat_id}", f"end_{chat_id}"]:
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

    was_active = session["state"] == "active"
    session["state"] = "cancelled"
    save_data(data)

    if was_active:
        await unlock_chat(context.bot, update.effective_chat.id)

    await update.message.reply_text(
        f"🚫 *Session cancelled by {user.full_name}.*\n"
        f"_Chat is unlocked. Start a new session whenever you're ready._",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Guard messages during session ─────────────────────────────────────────
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete messages sent by participants during locked session."""
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
        return  # on break, allowed to send

    try:
        await update.message.delete()
        mention = f"[{update.effective_user.first_name}](tg://user?id={uid})"
        await context.bot.send_message(
            update.effective_chat.id,
            f"📵 {mention} — focus mode is ON! No chatting until the session ends. 🔒",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError:
        pass

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN not set.")

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guard_messages))

    logger.info("StudyLock Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
