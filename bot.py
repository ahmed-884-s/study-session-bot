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

RESTRICTED = ChatPermissions(can_send_messages=False)

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
    "كفاية يا، التنسيق مش هييجي بالدردشة 🤦",
    "الرسالة في ذمة الله… وأنت لسه عليك باب كامل 😶",
    "كل مرة تبعت رسالة، في ورقة امتحان هناك بتضحك عليك 📝😈",
    "بطل فهلوة وارجع لذاكر قبل ما المنهج ينتقم 😤",
    "النظام رصد محاولة فشل دراسي وتم التعامل معها 🤖",
    "أنت داخل تذاكر ولا تفتح برنامج حواري؟ 🙃",
    "تم مسح الرسالة حفاظًا على ما تبقى من مستقبلك الدراسي 🎓",
    "اقفل الشات وافتح مستقبلك بقا يبني 🚀",
]

MOTIVATIONAL = [
    "⚡ استمر — كل دقيقة بتفرق!",
    "📖 بتبني مستقبلك دلوقتي. متوقفش.",
    "🔥 الاستمرارية أهم من الشدة. فضل مركّز!",
    "🧠 دماغك بتقوى مع كل صفحة.",
    "💪 الأبطال بيتصنعوا في لحظات زي دي.",
    "🌟 ساعة أقرب لهدفك. أنت قادر!",
    "🎯 ركّز. تنفس. كمّل.",
    "🚀 الجهد اللي بتبذله النهارده هيجيب نتيجة بكره.",
]

BREAK_OVER = [
    "☕ البريك خلص! جه وقت الشغل. 📚",
    "⏰ وقت الراحة انتهى — ارجع يا بطل! 💪",
    "🔔 البريك انتهى! مستقبلك هيشكرك. 🚀",
    "📚 اتشحنت؟ يلا ننقفل تاني! 🔒",
]

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

def mention(user_id: str, name: str) -> str:
    return f"[{name}](tg://user?id={user_id})"

# ── Restrict/Unrestrict individual users (NO chat-wide lock) ───────────────

async def restrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, RESTRICTED)
    except TelegramError as e:
        logger.warning(f"Restrict user failed: {e}")

async def unrestrict_user(bot, chat_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(chat_id, user_id, UNLOCKED)
    except TelegramError as e:
        logger.warning(f"Unrestrict user failed: {e}")

# ── Break keyboard builder ─────────────────────────────────────────────────

def break_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    durations = [10, 15, 20, 30]
    row1 = [InlineKeyboardButton(f"☕ {d} دقيقة", callback_data=f"break_{chat_id}_{d}") for d in durations[:2]]
    row2 = [InlineKeyboardButton(f"☕ {d} دقيقة", callback_data=f"break_{chat_id}_{d}") for d in durations[2:]]
    row3 = [InlineKeyboardButton("⏰ 45 دقيقة", callback_data=f"break_{chat_id}_45"),
            InlineKeyboardButton("🛏 نص ساعة", callback_data=f"break_{chat_id}_30")]
    # Custom options
    buttons = [
        [InlineKeyboardButton("☕ ١٠ دقايق", callback_data=f"break_{chat_id}_10"),
         InlineKeyboardButton("☕ ١٥ دقيقة", callback_data=f"break_{chat_id}_15")],
        [InlineKeyboardButton("☕ ٢٠ دقيقة", callback_data=f"break_{chat_id}_20"),
         InlineKeyboardButton("☕ ٣٠ دقيقة", callback_data=f"break_{chat_id}_30")],
        [InlineKeyboardButton("⏰ ٤٥ دقيقة", callback_data=f"break_{chat_id}_45"),
         InlineKeyboardButton("🛏 ساعة كاملة", callback_data=f"break_{chat_id}_60")],
    ]
    return InlineKeyboardMarkup(buttons)

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

    stats = get_stats(str(user.id))
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    await update.message.reply_text(
        f"📚 *سيشن مذاكرة جديد!*\n\n"
        f"👤 بدأه: {user.full_name}\n"
        f"⏱ المدة: *{fmt_duration(duration)}*\n"
        f"👥 المشاركين حتى دلوقتي: 1\n\n"
        f"⏳ *عندك 5 دقايق تنضم!*\n"
        f"السيشن هيبدأ: *{fmt_time(join_deadline)}*\n\n"
        f"اضغط الزر تانت 👇",
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
        await query.answer("❌ السيشن مش مفتوح للانضمام.", show_alert=True)
        return

    uid = str(user.id)
    if uid in session["participants"]:
        await query.answer("✅ أنت بالفعل في السيشن!", show_alert=True)
        return

    session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
    stats = get_stats(uid)
    stats["username"] = user.username or ""
    stats["name"] = user.full_name
    stats["sessions_joined"] += 1
    save_data(data)

    names = [p["name"] for p in session["participants"].values()]
    await query.answer("✅ انضممت للسيشن!", show_alert=True)

    keyboard = [[InlineKeyboardButton("✋ انضم للسيشن", callback_data=f"join_{chat_id}")]]
    await query.edit_message_text(
        f"📚 *سيشن مذاكرة — غرفة الانتظار*\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"👥 المشاركين ({len(names)}): {', '.join(names)}\n\n"
        f"⏳ السيشن هيبدأ في أقل من 5 دقايق!\n"
        f"اضغط الزر تانت 👇",
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
    save_data(data)

    participants = session["participants"]

    # Restrict all participants individually (NO chat-wide lock)
    for uid in participants:
        await restrict_user(context.bot, chat_int, int(uid))

    mentions = " ".join(mention(uid, p["name"]) for uid, p in participants.items())
    names_list = "\n".join(f"  • {p['name']}" for p in participants.values())

    await context.bot.send_message(
        chat_int,
        f"🔒 *السيشن بدأ!*\n\n"
        f"👥 المشاركين:\n{names_list}\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
        f"🏁 ينتهي: *{fmt_time(end.isoformat())}*\n\n"
        f"📵 *تم تقييد المشاركين.* وضع التركيز شغال!\n"
        f"_{mentions}_\n\n"
        f"_باقي الجروب يقدر يتكلم عادي_ 🙂",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Schedule hourly motivational messages
    for i in range(1, session["duration"] // 60 + 1):
        msg = MOTIVATIONAL[i % len(MOTIVATIONAL)]
        elapsed = fmt_duration(i * 60)
        context.job_queue.run_once(
            send_motivation_job,
            when=i * 3600,
            data={"chat_int": chat_int, "msg": msg, "elapsed": elapsed, "total": session["duration"]},
            name=f"motiv_{chat_id}_{i}",
        )

    # Schedule session end
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
        f"{d['msg']}\n\n⏱ *{d['elapsed']}* مضت",
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

    participants = dict(session["participants"])

    # Update stats
    for uid in participants:
        stats = get_stats(uid)
        stats["total_minutes"] += session["duration"]
        stats["sessions_completed"] += 1
        stats["last_study_date"] = now().date().isoformat()
        update_streak(uid)
        # Unrestrict participants
        await unrestrict_user(context.bot, chat_int, int(uid))

    session["state"] = "ended"
    save_data(data)

    # Build summary lines
    lines = []
    for uid, pinfo in participants.items():
        stats = get_stats(uid)
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        streak_str = f" 🔥 {streak}" if streak > 1 else ""
        lines.append(f"  • {pinfo['name']} — {fmt_duration(session['duration'])}{streak_str}")

    # Mention all participants
    mentions_text = " ".join(mention(uid, p["name"]) for uid, p in participants.items())

    await context.bot.send_message(
        chat_int,
        f"✅ *السيشن انتهى!*\n\n"
        f"🎉 أنتم أبطال فعلاً!\n"
        f"⏱ وقت المذاكرة: *{fmt_duration(session['duration'])}*\n\n"
        f"*المشاركين:*\n" + "\n".join(lines) + "\n\n"
        f"💤 تم فك التقييد عن الجميع. استحقوا الراحة!\n\n"
        f"{mentions_text}\n"
        f"خدوا بريك صغير وابدأوا سيشن جديد يا أبطال 🚀\n\n"
        f"اختار مدة البريك 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=break_keyboard(chat_id),
    )

# ── Break inline button callback ───────────────────────────────────────────

async def break_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid = str(user.id)

    parts = query.data.split("_")
    # format: break_{chat_id}_{minutes}
    # chat_id can be negative (e.g. -100123), so join middle parts
    minutes = int(parts[-1])
    chat_id = "_".join(parts[1:-1])

    session = get_session(chat_id)
    chat_int = update.effective_chat.id

    # Check if user was a participant
    if not session or uid not in session.get("participants", {}):
        await query.answer("❌ أنت مش من المشاركين في السيشن ده.", show_alert=True)
        return

    if uid in session.get("breaks", {}):
        await query.answer("⏰ أنت عندك بريك شغال بالفعل!", show_alert=True)
        return

    break_end = now() + timedelta(minutes=minutes)
    session.setdefault("breaks", {})[uid] = {
        "end": break_end.isoformat(),
        "duration": minutes,
        "name": user.full_name,
    }
    save_data(data)

    # Restrict user during break
    await restrict_user(context.bot, chat_int, user.id)

    await query.answer(f"✅ البريك بدأ — {minutes} دقيقة!", show_alert=True)

    await context.bot.send_message(
        chat_int,
        f"☕ *{user.full_name}* بدأ بريك!\n\n"
        f"⏱ المدة: *{minutes} دقيقة*\n"
        f"🔔 هترجع: *{fmt_time(break_end.isoformat())}*\n\n"
        f"_استرخي واتشحن — هنناديك لما الوقت ييجي 😴_",
        parse_mode=ParseMode.MARKDOWN,
    )

    context.job_queue.run_once(
        end_break_job,
        when=minutes * 60,
        data={"chat_id": chat_id, "chat_int": chat_int, "uid": uid, "name": user.full_name},
        name=f"break_{chat_id}_{uid}",
    )

# ── /break command (manual) ────────────────────────────────────────────────

async def cmd_break(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    session = get_session(chat_id)
    if not session or session["state"] not in ("ended", "active"):
        await update.message.reply_text(
            "☕ *اختار مدة البريك:* 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=break_keyboard(chat_id),
        )
        return

    if uid not in session.get("participants", {}):
        await update.message.reply_text("❌ أنت مش من المشاركين في السيشن.")
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
        "end": break_end.isoformat(),
        "duration": minutes,
        "name": user.full_name,
    }
    save_data(data)

    await restrict_user(context.bot, update.effective_chat.id, user.id)

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
        save_data(data)

    await unrestrict_user(context.bot, chat_int, int(uid))

    msg = random.choice(BREAK_OVER)
    men = mention(uid, name)

    await context.bot.send_message(
        chat_int,
        f"{msg}\n\n{men}، البريك خلص — ارجع للمذاكرة! 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Re-restrict if session still active
    session = get_session(chat_id)
    if session and session.get("state") == "active":
        await restrict_user(context.bot, chat_int, int(uid))
        await context.bot.send_message(
            chat_int,
            f"🔒 {men} رجع للمذاكرة! التقييد شغال تاني.",
            parse_mode=ParseMode.MARKDOWN,
        )

# ── /back (end break early) ────────────────────────────────────────────────

async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    session = get_session(chat_id)
    if not session or uid not in session.get("breaks", {}):
        await update.message.reply_text("❌ مفيش بريك شغال عندك.")
        return

    jobs = context.job_queue.get_jobs_by_name(f"break_{chat_id}_{uid}")
    for job in jobs:
        job.schedule_removal()

    del session["breaks"][uid]
    save_data(data)

    await unrestrict_user(context.bot, update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"💪 *{user.full_name}* خلص البريك بدري!\n"
        f"رجع للـ grinding 📚🔥",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /pomodoro ──────────────────────────────────────────────────────────────

async def cmd_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if update.effective_chat.type == "private":
        await update.message.reply_text("❌ الأمر ده بيشتغل في الجروبات بس.")
        return

    session = get_session(chat_id)
    if session and session.get("state") in ("waiting", "active"):
        await update.message.reply_text("⚠️ في سيشن شغال بالفعل.")
        return

    args = context.args
    cycles = int(args[0]) if args and args[0].isdigit() else 4
    cycles = max(1, min(cycles, 8))
    work_min = 25
    break_min = 5
    total = cycles * (work_min + break_min)

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
    save_data(data)

    keyboard = [[InlineKeyboardButton("✋ انضم للبومودورو", callback_data=f"join_{chat_id}")]]
    await update.message.reply_text(
        f"🍅 *سيشن بومودورو!*\n\n"
        f"👤 بدأه: {user.full_name}\n"
        f"🔄 الدورات: *{cycles}* × (25 دقيقة مذاكرة + 5 دقايق بريك)\n"
        f"⏱ الإجمالي: *{fmt_duration(total)}*\n\n"
        f"⏳ *5 دقايق للانضمام!*\n"
        f"اضغط الزر تانت 👇",
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
    state_text = "في الانتظار" if session["state"] == "waiting" else "شغال — وضع تركيز"

    msg = (
        f"{state_emoji} *حالة السيشن: {state_text}*\n\n"
        f"⏱ المدة: *{fmt_duration(session['duration'])}*\n"
    )
    if session.get("end_time"):
        msg += f"🏁 ينتهي: *{fmt_time(session['end_time'])}*\n"
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
        f"⏱ إجمالي وقت المذاكرة: *{hours}h {minutes}m*\n"
        f"✅ السيشنات المكتملة: *{stats['sessions_completed']}*\n"
        f"👥 السيشنات اللي انضممت ليها: *{stats['sessions_joined']}*\n"
        f"🔥 السلسلة الحالية: *{streak} يوم{'/' if streak != 1 else ''}*\n"
        f"📅 آخر مذاكرة: *{stats.get('last_study_date') or 'N/A'}*",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /leaderboard ───────────────────────────────────────────────────────────

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not data["stats"]:
        await update.message.reply_text("📊 مفيش داتا لسه! ابدأ سيشن مذاكرة.")
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
        lines.append(f"{medals[i]} *{name}* — {h}h {m}m{streak_str}")

    await update.message.reply_text(
        f"🏆 *ليدربورد المذاكرة*\n\n" + "\n".join(lines) + "\n\n_بيتحدث في الوقت الفعلي_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /end (leave session) ───────────────────────────────────────────────────

async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user
    uid = str(user.id)

    session = get_session(chat_id)
    if not session or session["state"] not in ("waiting", "active"):
        await update.message.reply_text("❌ مفيش سيشن شغال تغادره.")
        return

    if uid not in session["participants"]:
        await update.message.reply_text("❌ أنت مش من المشاركين.")
        return

    del session["participants"][uid]
    if session["state"] == "active":
        await unrestrict_user(context.bot, update.effective_chat.id, user.id)

    save_data(data)
    await update.message.reply_text(
        f"👋 *{user.full_name}* سيب السيشن.\n"
        f"_الاستمرارية هي المفتاح! نشوفك المرة الجاية._ 📚",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Guard: delete messages from restricted participants ────────────────────

async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete messages sent by participants during active session & reply with funny message."""
    if not update.message or update.effective_chat.type == "private":
        return

    chat_id = str(update.effective_chat.id)
    uid = str(update.effective_user.id)
    user = update.effective_user

    session = get_session(chat_id)
    if not session or session["state"] != "active":
        return

    if uid not in session["participants"]:
        return

    if uid in session.get("breaks", {}):
        return  # Allowed during break

    try:
        await update.message.delete()
        men = mention(uid, user.first_name)
        msg = random.choice(GUARD_MESSAGES)
        await context.bot.send_message(
            update.effective_chat.id,
            f"📵 {men}\n\n_{msg}_",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.warning(f"Guard failed: {e}")

# ── /start ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *StudyLock Bot*\n\n"
        "بساعد مجموعات المذاكرة تتركز بتقييد المشاركين أثناء السيشنات.\n\n"
        "*الأوامر:*\n"
        "`/study 2h` — ابدأ سيشن ساعتين\n"
        "`/pomodoro` — ابدأ سيشن بومودورو\n"
        "`/break` — خد بريك (بزرار أو رقم)\n"
        "`/back` — خلص البريك بدري\n"
        "`/status` — حالة السيشن الحالي\n"
        "`/stats` — إحصائياتك\n"
        "`/leaderboard` — ترتيب المجموعة\n"
        "`/end` — اغادر السيشن\n\n"
        "_ضيفني للجروب واعملني أدمن عشان أشتغل صح!_",
        parse_mode=ParseMode.MARKDOWN,
    )

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
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))

    app.add_handler(CallbackQueryHandler(join_callback, pattern=r"^join_"))
    app.add_handler(CallbackQueryHandler(break_callback, pattern=r"^break_"))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, guard_messages))

    logger.info("StudyLock Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
