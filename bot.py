import os
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

# ── Constants ──────────────────────────────────────────────────────────────
POINTS_PER_MINUTE = 1
POINTS_PENALTY    = 5
MAX_BREAK_MINUTES = 60

# ── Messages ───────────────────────────────────────────────────────────────
MOTIVATIONAL = [
    "استمر — كل دقيقة بتفرق ⚡",
    "بتتبني مستقبلك دلوقتي. ماتوقفش.",
    "الاستمرارية أهم من الشدة. ركز 🔥",
    "عقلك بيتقوى مع كل صفحة.",
    "الأبطال بيتصنعوا في لحظات زي دي 💪",
    "بقيت أقرب لهدفك بساعة. تقدر!",
    "ركز. اتنفس. كمل.",
    "المجهود اللي بتبذله النهارده هيدفع بكرة.",
    "العبقرية مش موهبة — هي تكرار ومجهود.",
    "الأوائل مش أذكى — هم بس أصبر.",
    "الفارق بينك وبين اللي تحسده هو ساعات المذاكرة.",
    "كل صفحة بتقلبها دي خطوة لأحلامك.",
]

HALFWAY_MESSAGES = [
    "نص الطريق خلص! كمل الجزء التاني وأنت أقوى ⚡",
    "نصّه خلص بجد! الجزء التاني أسهل دايماً — استمر!",
    "نص الوقت مضى! الواقفين هينجحوا، والقاعدين هيتفرجوا.",
]

BREAK_OVER = [
    "الراحة خلصت! وقت الشغل ☕",
    "وقت الراحة انتهى — ارجع يا بطل!",
    "الاستراحة خلصت! مستقبلك هتشكرك.",
    "الشحن خلص — نركز تاني!",
    "جسمك اتشحن — هيا نكمل!",
]

DELETE_MESSAGES = [
    "ياض حسّ على دمك واقفل بقا 😒📵",
    "تم اتخاذ الإجراءات القانونية ضد الرسالة بنجاح ⚖️",
    "سيب التليفون في حاله وكمل يا ابني، متتعبناش معاك 😤",
    "هو إحنا ناقصين تشتت؟ الرسالة راحت، ركّز الله يكرمك 🙏",
    "يا معلم، المنهج مش هيخلص نفسه بنفسه 📖",
    "كفاية عبث… الكتاب بيعيط في الركن 😢",
    "تم القبض على رسالتك بتهمة إزعاج المذاكرين 🚔",
    "اكتب بعدين براحتك، دلوقتي العب دور الطالب المجتهد 🎭",
    "إحنا في سيشن مذاكرة مش سهرة عائلية 🫠",
    "لو فتحت الكتاب زي ما فتحت الشات كان زمانك خلصت المنهج 15 مرة 📚",
    "الرسالة في ذمة الله… وأنت لسه عليك باب كامل 😶",
    "كل مرة تبعت رسالة، في ورقة امتحان هناك بتضحك عليك 📝😈",
    "النظام رصد محاولة فشل دراسي وتم التعامل معها 🤖",
    "تم مسح الرسالة حفاظًا على ما تبقى من مستقبلك الدراسي 🎓",
    "اقفل الشات وافتح مستقبلك بقا يبني 🚀",
]

# ── Data ───────────────────────────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {"sessions": {}, "stats": {}, "streaks": {}, "goals": {}}

def save_data(d: dict):
    DATA_FILE.write_text(json.dumps(d, default=str, ensure_ascii=False))

data = load_data()
data.setdefault("goals", {})

_announced_chats: set = set()

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
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}س {m}د"
    elif h:
        return f"{h}س"
    return f"{m}د"

def parse_duration(text: str) -> int | None:
    """يقبل: 1h  90m  1h30m  45 (دقايق كأرقام بس)"""
    text = text.lower().strip()
    if text.isdigit():
        total = int(text)
        return total if 10 <= total <= 480 else None
    match = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m?)?', text)
    if not match or not any(match.groups()):
        return None
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    total = h * 60 + m
    return total if 10 <= total <= 480 else None

def mention(user_id, name: str) -> str:
    return f"[{name}](tg://user?id={user_id})"

def get_stats(user_id: str) -> dict:
    return data["stats"].setdefault(user_id, {
        "total_minutes": 0, "sessions_completed": 0,
        "sessions_joined": 0, "last_study_date": None,
        "username": "", "name": "",
        "points": 0, "weekly_points": 0,
        "weekly_minutes": 0, "daily_minutes": 0,
        "badges": [],
    })

def update_user_info(user_id: str, full_name: str, username: str):
    s = get_stats(user_id)
    s["name"] = full_name
    s["username"] = username or ""

def update_streak(user_id: str):
    s = data["streaks"].setdefault(user_id, {"streak": 0, "last_date": None, "max_streak": 0})
    today     = now().date().isoformat()
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    if s["last_date"] == today:
        return
    s["streak"] = s["streak"] + 1 if s["last_date"] == yesterday else 1
    s["last_date"] = today
    if s["streak"] > s.get("max_streak", 0):
        s["max_streak"] = s["streak"]

def add_points(user_id: str, pts: int):
    s = get_stats(user_id)
    s["points"]        = max(0, s.get("points", 0) + pts)
    s["weekly_points"] = max(0, s.get("weekly_points", 0) + pts)

def check_and_award_badges(user_id: str) -> list[str]:
    stats  = get_stats(user_id)
    streak = data["streaks"].get(user_id, {}).get("streak", 0)
    badges = stats.setdefault("badges", [])
    rules  = [
        ("🌱 مبتدئ",     stats["sessions_completed"] >= 1),
        ("📚 مذاكر",     stats["sessions_completed"] >= 5),
        ("🏃 مداوم",     stats["sessions_completed"] >= 20),
        ("🏆 بطل",       stats["sessions_completed"] >= 50),
        ("⏱ ساعة",      stats["total_minutes"] >= 60),
        ("🕐 10 ساعات",  stats["total_minutes"] >= 600),
        ("🕑 50 ساعة",   stats["total_minutes"] >= 3000),
        ("🔥 3 أيام",    streak >= 3),
        ("🔥🔥 أسبوع",   streak >= 7),
        ("🔥🔥🔥 شهر",   streak >= 30),
        ("⭐ 100 نقطة",  stats["points"] >= 100),
        ("💎 1000 نقطة", stats["points"] >= 1000),
    ]
    new = [name for name, cond in rules if cond and name not in badges]
    badges.extend(new)
    return new

def build_leaderboard_text(title: str, sort_key: str = "points") -> str:
    if not data["stats"]:
        return "لسه مفيش بيانات. ابدأ سيشن مذاكرة."
    users  = sorted(data["stats"].items(), key=lambda x: x[1].get(sort_key, 0), reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines  = []
    for i, (uid, s) in enumerate(users):
        pts = s.get(sort_key, 0)
        if pts == 0:
            continue
        name   = s.get("name") or s.get("username") or f"مستخدم {uid}"
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        fire   = f" 🔥{streak}" if streak > 1 else ""
        mins   = s.get("weekly_minutes" if sort_key == "weekly_points" else "total_minutes", 0)
        h, m   = divmod(mins, 60)
        lines.append(f"{medals[i]} *{name}* — {pts} نقطة ({h}س {m}د){fire}")
    if not lines:
        return f"*{title}*\n\nلسه مفيش بيانات كافية."
    return f"*{title}*\n\n" + "\n".join(lines) + "\n\n_بيتحدث تلقائياً_"

# ── Pin helpers ────────────────────────────────────────────────────────────
async def pin_msg(bot, chat_id: int, message_id: int):
    try:
        await bot.pin_chat_message(chat_id, message_id, disable_notification=True)
    except TelegramError as e:
        logger.warning(f"pin failed: {e}")

async def unpin_msg(bot, chat_id: int, message_id: int):
    try:
        await bot.unpin_chat_message(chat_id, message_id)
    except TelegramError as e:
        logger.warning(f"unpin failed: {e}")

async def maybe_announce(bot, chat_id: int):
    pass  # disabled

async def is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False

def cancel_jobs(job_queue, prefixes: list[str]):
    for job in job_queue.jobs():
        if job.name and any(job.name.startswith(p) for p in prefixes):
            job.schedule_removal()

# ── /start ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *StudyLock Bot*\n"
        "_بوت لتنظيم جلسات المذاكرة في المجموعات_\n\n"
        "*السيشن:*\n"
        "· /study 1h — ابدأ سيشن\n"
        "· /study 1h30m الرياضيات — مع موضوع\n"
        "· /pomodoro أو /pomodoro 6 — بومودورو\n"
        "· /join — انضم لأي سيشن شغالة\n"
        "· /break 10 — استراحة (أو /break بس = 10 دقايق)\n"
        "· /back — رجوع بدري من الاستراحة\n"
        "· /end — اخرج من السيشن\n"
        "· /status — حالة السيشن\n\n"
        "*إحصائيات:*\n"
        "· /stats — إحصائياتك\n"
        "· /goal 2h — هدفك اليومي\n"
        "· /leaderboard — الترتيب الكلي\n"
        "· /weekly — ترتيب الأسبوع\n"
        "· /badges — شاراتك\n\n"
        "*للأدمن:*\n"
        "· /reset — تنظيف السيشنات\n\n"
        "_ضيفني في المجموعة واعملني أدمن!_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /study ─────────────────────────────────────────────────────────────────
async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("الأمر ده بيشتغل في المجموعات بس.")
        return
    await maybe_announce(context.bot, update.effective_chat.id)

    chat_id = str(update.effective_chat.id)
    user    = update.effective_user
    args    = context.args

    if not args:
        await update.message.reply_text(
            "*الاستخدام:* `/study <المدة> [الموضوع]`\n\n"
            "أمثلة:\n`/study 1h`\n`/study 90m`\n`/study 1h30m الرياضيات`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    duration = parse_duration(args[0])
    if not duration:
        await update.message.reply_text(
            "مدة مش صحيحة — استخدم مثلاً `1h` أو `90m` أو `1h30m`\n"
            "_الحد الأدنى 10 دقائق، الأقصى 8 ساعات_",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    topic = " ".join(args[1:]) if len(args) > 1 else None
    await _create_session(update, context, chat_id, user, duration, topic, pomodoro=False)

# ── /pomodoro ──────────────────────────────────────────────────────────────
async def cmd_pomodoro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("الأمر ده بيشتغل في المجموعات بس.")
        return
    await maybe_announce(context.bot, update.effective_chat.id)

    chat_id  = str(update.effective_chat.id)
    user     = update.effective_user
    args     = context.args
    cycles   = max(1, min(int(args[0]) if args and args[0].isdigit() else 4, 8))
    work_min, brk_min = 25, 5
    total    = cycles * (work_min + brk_min)
    await _create_session(update, context, chat_id, user, total, topic=None,
                          pomodoro=True, cycles=cycles, work=work_min, brk=brk_min)

# ── Shared session creator ─────────────────────────────────────────────────
async def _create_session(update, context, chat_id, user, duration, topic,
                           pomodoro=False, cycles=4, work=25, brk=5):
    uid        = str(user.id)
    session_id = f"{'p' if pomodoro else 's'}{now().strftime('%H%M%S')}{random.randint(100,999)}"
    sessions   = data["sessions"].setdefault(chat_id, {})

    sessions[session_id] = {
        "state": "waiting",
        "duration": duration, "topic": topic,
        "started_by": uid,
        "participants": {uid: {"name": user.full_name, "username": user.username or ""}},
        "start_time": None, "end_time": None, "breaks": {},
        "pomodoro": pomodoro, "pomo_cycles": cycles,
        "pomo_work": work, "pomo_break": brk, "pomo_cycle": 0,
        "pinned_message_id": None, "active_pinned_message_id": None,
    }
    update_user_info(uid, user.full_name, user.username or "")
    get_stats(uid)["sessions_joined"] += 1
    save_data(data)

    topic_line = f"\nالموضوع: *{topic}*" if topic else ""
    header = f"🍅 *سيشن بومودورو*\nالدورات: *{cycles}* × (25د مذاكرة + 5د استراحة)" if pomodoro else "📚 *سيشن مذاكرة*"

    keyboard = [[InlineKeyboardButton("✋ انضم", callback_data=f"join_{chat_id}_{session_id}")]]
    msg = await update.message.reply_text(
        f"{header}\n"
        f"· بدأها: {user.full_name}\n"
        f"· المدة: *{fmt_duration(duration)}*"
        f"{topic_line}\n"
        f"· المشاركين: 1\n\n"
        f"_السيشن هتبدأ على طول — اضغط للانضمام_ 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    sessions[session_id]["pinned_message_id"] = msg.message_id
    save_data(data)
    await pin_msg(context.bot, update.effective_chat.id, msg.message_id)

    context.job_queue.run_once(
        start_session_job, when=2,
        data={"chat_id": chat_id, "session_id": session_id, "chat_int": update.effective_chat.id},
        name=f"start_{chat_id}_{session_id}",
    )

# ── /join — بديل نصي للزرار ───────────────────────────────────────────────
async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("الأمر ده بيشتغل في المجموعات بس.")
        return

    chat_id  = str(update.effective_chat.id)
    user     = update.effective_user
    uid      = str(user.id)
    sessions = data["sessions"].get(chat_id, {})

    target, target_sid = None, None
    for s_id, s in sessions.items():
        if s.get("state") in ("waiting", "active"):
            target, target_sid = s, s_id
            break

    if not target:
        await update.message.reply_text(
            "مفيش سيشن شغالة دلوقتي.\nابدأ واحدة بـ `/study 1h`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if uid in target["participants"]:
        await update.message.reply_text("انت شارك في السيشن أصلاً! 👍")
        return

    target["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
    update_user_info(uid, user.full_name, user.username or "")
    get_stats(uid)["sessions_joined"] += 1
    save_data(data)

    names      = [p["name"] for p in target["participants"].values()]
    topic_line = f"\nالموضوع: *{target['topic']}*" if target.get("topic") else ""
    state_text = "بتستنى" if target["state"] == "waiting" else "شغالة"

    await update.message.reply_text(
        f"✅ *{user.full_name}* انضم!\n"
        f"· المدة: *{fmt_duration(target['duration'])}*{topic_line}\n"
        f"· الحالة: {state_text}\n"
        f"· المشاركين ({len(names)}): {', '.join(names)}",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Join button callback ───────────────────────────────────────────────────
async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = query.from_user
    _, chat_id, session_id = query.data.split("_", 2)

    sessions = data["sessions"].get(chat_id, {})
    session  = sessions.get(session_id)

    if not session or session["state"] not in ("waiting", "active"):
        await query.answer("السيشن دي اتقفلت أو انتهت.", show_alert=True)
        return

    uid = str(user.id)
    if uid in session["participants"]:
        await query.answer("انت شارك أصلاً! 👍", show_alert=True)
        return

    session["participants"][uid] = {"name": user.full_name, "username": user.username or ""}
    update_user_info(uid, user.full_name, user.username or "")
    get_stats(uid)["sessions_joined"] += 1
    save_data(data)

    names      = [p["name"] for p in session["participants"].values()]
    topic_line = f"\nالموضوع: *{session['topic']}*" if session.get("topic") else ""
    state_text = "بتستنى" if session["state"] == "waiting" else "شغالة"
    keyboard   = [[InlineKeyboardButton("✋ انضم", callback_data=f"join_{chat_id}_{session_id}")]]

    try:
        await query.edit_message_text(
            f"📚 *سيشن المذاكرة — {state_text}*\n\n"
            f"المدة: *{fmt_duration(session['duration'])}*{topic_line}\n"
            f"المشاركين ({len(names)}): {', '.join(names)}\n\n"
            f"_اضغط للانضمام_ 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except TelegramError:
        pass

    await query.answer("تمام أنت انضميت دلوقتي ✅", show_alert=True)

# ── Start session job ──────────────────────────────────────────────────────
async def start_session_job(context: ContextTypes.DEFAULT_TYPE):
    d          = context.job.data
    chat_id    = d["chat_id"]
    session_id = d["session_id"]
    chat_int   = d["chat_int"]

    sessions = data["sessions"].get(chat_id, {})
    session  = sessions.get(session_id)
    if not session or session["state"] != "waiting":
        return

    start = now()
    end   = start + timedelta(minutes=session["duration"])
    session.update(state="active", start_time=start.isoformat(), end_time=end.isoformat())
    save_data(data)

    names      = [p["name"] for p in session["participants"].values()]
    topic_line = f"\nالموضوع: *{session['topic']}*" if session.get("topic") else ""
    keyboard   = [[InlineKeyboardButton("✋ انضم", callback_data=f"join_{chat_id}_{session_id}")]]

    # تعديل رسالة الـ waiting بدل إرسال رسالة جديدة
    old_pin = session.get("pinned_message_id")
    edited  = False
    if old_pin:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_int,
                message_id=old_pin,
                text=(
                    f"🔒 *السيشن بدأت!*\n\n"
                    f"المشاركين: {', '.join(names)}\n"
                    f"المدة: *{fmt_duration(session['duration'])}*{topic_line}\n"
                    f"تنتهي: *{fmt_time(end.isoformat())}*\n\n"
                    f"_الرسايل هتتمسح — ركز! ممكن تنضم لسه_ 👇"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            edited = True
            session["active_pinned_message_id"] = old_pin
        except TelegramError:
            pass

    if not edited:
        sent = await context.bot.send_message(
            chat_int,
            f"🔒 *السيشن بدأت!*\n\n"
            f"المشاركين: {', '.join(names)}\n"
            f"المدة: *{fmt_duration(session['duration'])}*{topic_line}\n"
            f"تنتهي: *{fmt_time(end.isoformat())}*\n\n"
            f"_الرسايل هتتمسح — ركز! ممكن تنضم لسه_ 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        await pin_msg(context.bot, chat_int, sent.message_id)
        session["active_pinned_message_id"] = sent.message_id

    save_data(data)

    dur = session["duration"]

    # تحفيز كل ساعة
    for i in range(1, dur // 60 + 1):
        context.job_queue.run_once(
            send_motivation_job, when=i * 3600,
            data={"chat_int": chat_int, "msg": MOTIVATIONAL[i % len(MOTIVATIONAL)], "elapsed": fmt_duration(i * 60)},
            name=f"motiv_{chat_id}_{session_id}_{i}",
        )

    # رسالة نص الوقت
    half = (dur * 60) // 2
    if half > 120:
        context.job_queue.run_once(
            send_halfway_job, when=half,
            data={"chat_int": chat_int, "duration": dur},
            name=f"halfway_{chat_id}_{session_id}",
        )

    # تحذير قبل النهاية بـ 5 دقائق
    if dur > 10:
        context.job_queue.run_once(
            send_warning_job, when=(dur - 5) * 60,
            data={"chat_int": chat_int},
            name=f"warn_{chat_id}_{session_id}",
        )

    # countdown — تحديث الرسالة المثبتة كل دقيقة
    context.job_queue.run_repeating(
        countdown_job, interval=60, first=60,
        data={"chat_id": chat_id, "session_id": session_id, "chat_int": chat_int},
        name=f"countdown_{chat_id}_{session_id}",
    )

    # نهاية السيشن
    context.job_queue.run_once(
        end_session_job, when=dur * 60,
        data={"chat_id": chat_id, "session_id": session_id, "chat_int": chat_int},
        name=f"end_{chat_id}_{session_id}",
    )

# ── Halfway ────────────────────────────────────────────────────────────────
async def send_halfway_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await context.bot.send_message(
        d["chat_int"],
        f"*{random.choice(HALFWAY_MESSAGES)}*\n_مضى {fmt_duration(d['duration'] // 2)} من أصل {fmt_duration(d['duration'])}_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── 5-min warning ──────────────────────────────────────────────────────────
async def send_warning_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        context.job.data["chat_int"],
        "⏳ *باقي 5 دقائق!* كمّل على آخرها 💪",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Countdown (edit pinned message every minute) ───────────────────────────
async def countdown_job(context: ContextTypes.DEFAULT_TYPE):
    d          = context.job.data
    chat_id    = d["chat_id"]
    session_id = d["session_id"]
    chat_int   = d["chat_int"]

    sessions = data["sessions"].get(chat_id, {})
    session  = sessions.get(session_id)
    if not session or session["state"] != "active":
        context.job.schedule_removal()
        return

    try:
        end_dt = datetime.fromisoformat(session["end_time"])
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=TZ)
        rem = (end_dt - now()).total_seconds()
        if rem <= 0:
            context.job.schedule_removal()
            return
        rem_min = int(rem // 60)
        rem_sec = int(rem % 60)
        rem_str = f"{rem_min}د {rem_sec:02d}ث" if rem_min else f"{rem_sec}ث"
    except Exception:
        return

    pin_id = session.get("active_pinned_message_id")
    if not pin_id:
        return

    names      = [p["name"] for p in session["participants"].values()]
    breaks     = session.get("breaks", {})
    topic_line = f"\n· الموضوع: *{session['topic']}*" if session.get("topic") else ""
    keyboard   = [[InlineKeyboardButton("✋ انضم", callback_data=f"join_{chat_id}_{session_id}")]]

    studying = [p["name"] for uid, p in session["participants"].items() if uid not in breaks]
    on_break = [p["name"] for uid, p in session["participants"].items() if uid in breaks]

    participants_text = "\n".join(f"· {n}" for n in studying)
    if on_break:
        participants_text += "\n" + "\n".join(f"· {n} ☕" for n in on_break)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_int,
            message_id=pin_id,
            text=(
                f"🔒 *السيشن شغالة*\n"
                f"· المتبقي: *{rem_str}*\n"
                f"· تنتهي: *{fmt_time(session['end_time'])}*"
                f"{topic_line}\n\n"
                f"*المشاركين ({len(names)}):*\n{participants_text}\n\n"
                f"_الرسايل هتتمسح — ركز! ممكن تنضم لسه_ 👇"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except TelegramError:
        pass  # الرسالة اتعدلت مؤخراً أو مفيش تغيير

# ── Motivation ─────────────────────────────────────────────────────────────
async def send_motivation_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await context.bot.send_message(
        d["chat_int"],
        f"{d['msg']}\n_مضى {d['elapsed']}_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── End session ────────────────────────────────────────────────────────────
async def end_session_job(context: ContextTypes.DEFAULT_TYPE):
    d          = context.job.data
    chat_id    = d["chat_id"]
    session_id = d["session_id"]
    chat_int   = d["chat_int"]

    sessions = data["sessions"].get(chat_id, {})
    session  = sessions.get(session_id)
    if not session:
        return

    earned         = session["duration"] * POINTS_PER_MINUTE
    new_badges_all = []

    for uid, pinfo in session["participants"].items():
        s = get_stats(uid)
        s["total_minutes"]      += session["duration"]
        s["weekly_minutes"]      = s.get("weekly_minutes", 0) + session["duration"]
        s["daily_minutes"]       = s.get("daily_minutes", 0) + session["duration"]
        s["sessions_completed"] += 1
        s["last_study_date"]     = now().date().isoformat()
        add_points(uid, earned)
        update_streak(uid)
        nb = check_and_award_badges(uid)
        if nb:
            new_badges_all.append((pinfo["name"], nb))

    session["state"] = "ended"
    save_data(data)

    if pin := session.get("active_pinned_message_id"):
        await unpin_msg(context.bot, chat_int, pin)

    medals       = ["🥇", "🥈", "🥉"] + ["🏅"] * 20
    sorted_parts = sorted(
        session["participants"].items(),
        key=lambda x: get_stats(x[0]).get("points", 0), reverse=True
    )
    lines = []
    for i, (uid, pinfo) in enumerate(sorted_parts):
        pts    = get_stats(uid).get("points", 0)
        streak = data["streaks"].get(uid, {}).get("streak", 0)
        fire   = f" 🔥{streak}" if streak > 1 else ""
        lines.append(f"{medals[i]} {pinfo['name']} — +{earned} نقطة (المجموع: {pts}){fire}")

    badge_text = ""
    if new_badges_all:
        bl = [f"*{n}* فتح: {' '.join(b)}" for n, b in new_badges_all]
        badge_text = "\n\n🎖 *شارات جديدة!*\n" + "\n".join(bl)

    topic_line = f"\nالموضوع: *{session['topic']}*" if session.get("topic") else ""

    await context.bot.send_message(
        chat_int,
        f"✅ *السيشن انتهت!*\n"
        f"· وقت المذاكرة: *{fmt_duration(session['duration'])}*{topic_line}\n\n"
        f"*الترتيب:*\n" + "\n".join(lines) +
        badge_text +
        "\n\n_/stats لإحصائياتك — /break لتايمر استراحة_",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── Empty session cleanup ──────────────────────────────────────────────────
async def check_and_end_empty_session(context, chat_id: str, session_id: str):
    sessions = data["sessions"].get(chat_id, {})
    session  = sessions.get(session_id)
    if session and len(session.get("participants", {})) == 0:
        cancel_jobs(context.job_queue, [
            f"end_{chat_id}_{session_id}",
            f"motiv_{chat_id}_{session_id}",
            f"halfway_{chat_id}_{session_id}",
            f"warn_{chat_id}_{session_id}",
            f"countdown_{chat_id}_{session_id}",
        ])
        if pin := session.get("active_pinned_message_id"):
            await unpin_msg(context.bot, int(chat_id), pin)
        del sessions[session_id]
        save_data(data)

# ── /break ─────────────────────────────────────────────────────────────────
async def cmd_break(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("الأمر ده بيشتغل في المجموعات بس.")
        return

    chat_id  = str(update.effective_chat.id)
    user     = update.effective_user
    uid      = str(user.id)
    sessions = data["sessions"].get(chat_id, {})

    target_session, target_sid = None, None
    for s_id, s in sessions.items():
        if s.get("state") == "active" and uid in s.get("participants", {}):
            target_session, target_sid = s, s_id
            break

    if not target_session:
        await update.message.reply_text(
            "مش موجود في سيشن شغالة. ابدأ بـ `/study`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # في استراحة أصلاً؟
    if uid in target_session.get("breaks", {}):
        brk_end = target_session["breaks"][uid]["end"]
        await update.message.reply_text(
            f"انت أصلاً في استراحة تخلص الساعة *{fmt_time(brk_end)}*\n"
            f"استخدم /back لو حابب ترجع بدري.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    args = context.args
    # /break بدون أرقام = 10 دقايق افتراضي
    if not args:
        minutes = 10
    else:
        try:
            minutes = int(args[0])
            if not 1 <= minutes <= MAX_BREAK_MINUTES:
                raise ValueError
        except ValueError:
            await update.message.reply_text(f"المدة لازم تكون بين 1 و {MAX_BREAK_MINUTES} دقيقة.")
            return

    break_end = now() + timedelta(minutes=minutes)
    target_session["breaks"][uid] = {"end": break_end.isoformat(), "duration": minutes, "name": user.full_name}
    save_data(data)

    await update.message.reply_text(
        f"☕ *{user.full_name}* في استراحة *{minutes} دقيقة*\n"
        f"· ترجع الساعة: *{fmt_time(break_end.isoformat())}*\n"
        f"_رسايلك مش هتتمسح_",
        parse_mode=ParseMode.MARKDOWN,
    )
    context.job_queue.run_once(
        end_break_job, when=minutes * 60,
        data={"chat_id": chat_id, "session_id": target_sid, "chat_int": update.effective_chat.id,
              "uid": uid, "name": user.full_name},
        name=f"break_{chat_id}_{target_sid}_{uid}",
    )

# ── End break job ──────────────────────────────────────────────────────────
async def end_break_job(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    chat_id, session_id, chat_int, uid, name = \
        d["chat_id"], d["session_id"], d["chat_int"], d["uid"], d["name"]

    sessions = data["sessions"].get(chat_id, {})
    if s := sessions.get(session_id):
        s.get("breaks", {}).pop(uid, None)
        save_data(data)

    await context.bot.send_message(
        chat_int,
        f"{random.choice(BREAK_OVER)}\n{mention(uid, name)} استراحتك خلصت — ارجع تذاكر!",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /back ──────────────────────────────────────────────────────────────────
async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = str(update.effective_chat.id)
    uid      = str(update.effective_user.id)
    sessions = data["sessions"].get(chat_id, {})

    target_sid = next((s_id for s_id, s in sessions.items() if uid in s.get("breaks", {})), None)
    if not target_sid:
        await update.message.reply_text("مفيش استراحة شغالة ليك.")
        return

    cancel_jobs(context.job_queue, [f"break_{chat_id}_{target_sid}_{uid}"])
    sessions[target_sid]["breaks"].pop(uid, None)
    save_data(data)

    await update.message.reply_text(
        f"*{update.effective_user.full_name}* رجع بدري — هيا نكمل 💪",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /end ───────────────────────────────────────────────────────────────────
async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = str(update.effective_chat.id)
    user     = update.effective_user
    uid      = str(user.id)
    sessions = data["sessions"].get(chat_id, {})

    target_session, target_sid = None, None
    for s_id, s in sessions.items():
        if s.get("state") in ("waiting", "active") and uid in s.get("participants", {}):
            target_session, target_sid = s, s_id
            break

    if not target_session:
        await update.message.reply_text("مفيش سيشن شغالة تسيبها.")
        return

    # اطلع من الاستراحة كمان لو موجود
    target_session.get("breaks", {}).pop(uid, None)
    cancel_jobs(context.job_queue, [f"break_{chat_id}_{target_sid}_{uid}"])

    del target_session["participants"][uid]
    save_data(data)

    await update.message.reply_text(
        f"*{user.full_name}* ساب السيشن.\n_المداومة هي المفتاح — شوفك المرة الجاية!_",
        parse_mode=ParseMode.MARKDOWN,
    )

    if len(target_session["participants"]) == 0:
        await context.bot.send_message(update.effective_chat.id, "مفيش مشاركين — السيشن اتألغت.")
        await check_and_end_empty_session(context, chat_id, target_sid)

# ── /status ────────────────────────────────────────────────────────────────
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = str(update.effective_chat.id)
    sessions = data["sessions"].get(chat_id, {})
    active   = [(s_id, s) for s_id, s in sessions.items() if s.get("state") in ("waiting", "active")]

    if not active:
        await update.message.reply_text(
            "مفيش سيشن شغالة.\nابدأ واحدة بـ `/study 1h`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    for s_id, session in active:
        breaks     = session.get("breaks", {})
        state_text = "بتستنى مشاركين" if session["state"] == "waiting" else "شغالة"
        topic_line = f"\nالموضوع: *{session['topic']}*" if session.get("topic") else ""

        remaining_text = ""
        if session.get("end_time") and session["state"] == "active":
            try:
                end_dt = datetime.fromisoformat(session["end_time"])
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=TZ)
                rem = (end_dt - now()).total_seconds()
                if rem > 0:
                    remaining_text = f"\n· متبقي: *{fmt_duration(int(rem // 60))}*"
            except Exception:
                pass

        lines = []
        for uid, pinfo in session["participants"].items():
            if uid in breaks:
                lines.append(f"· {pinfo['name']} ☕ — استراحة (ترجع {fmt_time(breaks[uid]['end'])})")
            else:
                lines.append(f"· {pinfo['name']} — بيذاكر 📖")

        msg = (
            f"*السيشن — {state_text}*\n"
            f"· المدة: *{fmt_duration(session['duration'])}*{topic_line}"
        )
        if session.get("end_time"):
            msg += f"\n· تنتهي: *{fmt_time(session['end_time'])}*"
        msg += remaining_text
        msg += f"\n\n*المشاركين ({len(lines)}):*\n" + "\n".join(lines)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ── /stats ─────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid        = str(update.effective_user.id)
    stats      = get_stats(uid)
    streak     = data["streaks"].get(uid, {}).get("streak", 0)
    max_streak = data["streaks"].get(uid, {}).get("max_streak", 0)
    h, m       = divmod(stats["total_minutes"], 60)
    badges     = stats.get("badges", [])
    badge_txt  = " ".join(badges) if badges else "—"

    goal_text = ""
    if goal := data["goals"].get(uid):
        daily   = stats.get("daily_minutes", 0)
        pct     = min(100, int((daily / goal) * 100))
        filled  = pct // 10
        bar     = "█" * filled + "░" * (10 - filled)
        goal_text = f"\n\n*الهدف اليومي:* [{bar}] {pct}%\n_مذاكرت {fmt_duration(daily)} من أصل {fmt_duration(goal)}_"

    await update.message.reply_text(
        f"📊 *إحصائياتك*\n\n"
        f"· وقت المذاكرة: *{h}س {m}د*\n"
        f"· سيشنات اكتملت: *{stats['sessions_completed']}*\n"
        f"· سلسلة الأيام: *{streak}* (أعلى: *{max_streak}*)\n"
        f"· النقاط: *{stats.get('points', 0)}*\n"
        f"· آخر يوم: *{stats.get('last_study_date') or '—'}*\n"
        f"· الشارات: {badge_txt}"
        f"{goal_text}",
        parse_mode=ParseMode.MARKDOWN,
    )

# ── /goal ──────────────────────────────────────────────────────────────────
async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = str(update.effective_user.id)
    args = context.args

    if not args:
        cur = data["goals"].get(uid)
        txt = (f"هدفك اليومي: *{fmt_duration(cur)}*\n"
               f"_غيّره بـ `/goal 2h` أو امسحه بـ `/goal clear`_") if cur else \
              "مفيش هدف يومي. حدّده بـ `/goal 2h`"
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
        return

    if args[0].lower() == "clear":
        data["goals"].pop(uid, None)
        save_data(data)
        await update.message.reply_text("تم مسح الهدف اليومي.")
        return

    dur = parse_duration(args[0])
    if not dur:
        await update.message.reply_text("مدة مش صحيحة. استخدم مثلاً `/goal 2h`", parse_mode=ParseMode.MARKDOWN)
        return

    data["goals"][uid] = dur
    save_data(data)
    await update.message.reply_text(f"✅ هدفك اليومي: *{fmt_duration(dur)}*", parse_mode=ParseMode.MARKDOWN)

# ── /leaderboard & /weekly ─────────────────────────────────────────────────
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_leaderboard_text("🏆 الترتيب الكلي"), parse_mode=ParseMode.MARKDOWN)

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_leaderboard_text("🏆 ترتيب الأسبوع", "weekly_points"), parse_mode=ParseMode.MARKDOWN)

# ── /badges ────────────────────────────────────────────────────────────────
async def cmd_badges(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = str(update.effective_user.id)
    badges = get_stats(uid).get("badges", [])
    all_b  = [
        ("🌱 مبتدئ",     "أكمل سيشن واحدة"),
        ("📚 مذاكر",     "أكمل 5 سيشنات"),
        ("🏃 مداوم",     "أكمل 20 سيشن"),
        ("🏆 بطل",       "أكمل 50 سيشن"),
        ("⏱ ساعة",      "ذاكر ساعة"),
        ("🕐 10 ساعات",  "ذاكر 10 ساعات"),
        ("🕑 50 ساعة",   "ذاكر 50 ساعة"),
        ("🔥 3 أيام",    "سلسلة 3 أيام"),
        ("🔥🔥 أسبوع",   "سلسلة 7 أيام"),
        ("🔥🔥🔥 شهر",   "سلسلة 30 يوم"),
        ("⭐ 100 نقطة",  "اجمع 100 نقطة"),
        ("💎 1000 نقطة", "اجمع 1000 نقطة"),
    ]
    lines = [f"{'✅' if name in badges else '🔒'} {name} — _{desc}_" for name, desc in all_b]
    await update.message.reply_text("*شاراتك* 🎖\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# ── /reset ─────────────────────────────────────────────────────────────────
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(context.bot, update.effective_chat.id, update.effective_user.id):
        await update.message.reply_text("الأمر ده للأدمن بس.")
        return

    chat_id  = str(update.effective_chat.id)
    sessions = data["sessions"].get(chat_id, {})
    if not sessions:
        await update.message.reply_text("مفيش سيشنات شغالة عشان تتنضف.")
        return

    for s_id, s in sessions.items():
        for pk in ("active_pinned_message_id", "pinned_message_id"):
            if pid := s.get(pk):
                await unpin_msg(context.bot, update.effective_chat.id, pid)
        cancel_jobs(context.job_queue, [
            f"start_{chat_id}_{s_id}", f"end_{chat_id}_{s_id}",
            f"motiv_{chat_id}_{s_id}", f"halfway_{chat_id}_{s_id}",
            f"warn_{chat_id}_{s_id}", f"countdown_{chat_id}_{s_id}",
        ])

    del data["sessions"][chat_id]
    save_data(data)
    await update.message.reply_text("تم تنظيف كل السيشنات ✅")

# ── Mute/Unmute (admin reply) ──────────────────────────────────────────────

MUTE_PATTERN  = re.compile(r'كتفه يا بوت(?:\s+لمدة\s*(.+))?', re.IGNORECASE)
UNMUTE_PATTERN = re.compile(r'فكه يا بوت', re.IGNORECASE)

def parse_mute_duration(text: str | None) -> timedelta | None:
    """يحول النص لـ timedelta — مثلاً: ساعة، يوم، 3 ساعات، 30 دقيقة، أسبوع"""
    if not text:
        return timedelta(hours=1)  # افتراضي ساعة
    text = text.strip()
    patterns = [
        (r'(\d+)\s*دقيق[ةه]?', lambda m: timedelta(minutes=int(m.group(1)))),
        (r'(\d+)\s*ساع[ةه]?',  lambda m: timedelta(hours=int(m.group(1)))),
        (r'(\d+)\s*يوم',       lambda m: timedelta(days=int(m.group(1)))),
        (r'(\d+)\s*أسبوع',     lambda m: timedelta(weeks=int(m.group(1)))),
        (r'(\d+)\s*شهر',       lambda m: timedelta(days=int(m.group(1)) * 30)),
        (r'(\d+)\s*سن[ةه]',    lambda m: timedelta(days=int(m.group(1)) * 365)),
        (r'دقيق[ةه]',          lambda m: timedelta(minutes=1)),
        (r'ساع[ةه]',           lambda m: timedelta(hours=1)),
        (r'يوم',               lambda m: timedelta(days=1)),
        (r'أسبوع',             lambda m: timedelta(weeks=1)),
        (r'شهر',               lambda m: timedelta(days=30)),
    ]
    for pattern, fn in patterns:
        m = re.search(pattern, text)
        if m:
            return fn(m)
    return timedelta(hours=1)

def fmt_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:    parts.append(f"{days} يوم")
    if hours:   parts.append(f"{hours} ساعة")
    if minutes: parts.append(f"{minutes} دقيقة")
    return " و ".join(parts) if parts else "ساعة"

async def handle_mute_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type == "private":
        return

    text = update.message.text or ""
    user = update.effective_user
    chat = update.effective_chat

    # تحقق إن المرسل أدمن
    if not await is_admin(context.bot, chat.id, user.id):
        return

    # ── فك الكتف ──────────────────────────────────────────────────────────
    if UNMUTE_PATTERN.search(text):
        reply = update.message.reply_to_message
        if not reply:
            await update.message.reply_text("رد على رسالة الشخص اللي عايز تفكه.")
            return
        target = reply.from_user
        try:
            await context.bot.restrict_chat_member(
                chat.id, target.id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_invite_users=True,
                ),
            )
            await update.message.reply_text(
                f"✅ تم فك الكتف عن *{target.full_name}*",
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            await update.message.reply_text(f"مش قادر أفك الكتف: {e}")
        return

    # ── كتف ───────────────────────────────────────────────────────────────
    m = MUTE_PATTERN.search(text)
    if not m:
        return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text("رد على رسالة الشخص اللي عايز تكتفه.")
        return

    target   = reply.from_user
    duration = parse_mute_duration(m.group(1))
    until    = now() + duration

    try:
        await context.bot.restrict_chat_member(
            chat.id, target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        await update.message.reply_text(
            f"🔇 تم تكتيف *{target.full_name}* لمدة *{fmt_timedelta(duration)}*\n"
            f"· الكتف ينتهي: *{fmt_time(until.isoformat())}*\n\n"
            f"_قول \"فكه يا بوت\" وأنت رادّ على أي رسالته عشان تفكه بدري_",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        await update.message.reply_text(f"مش قادر أكتف: {e}")

# ── Guard messages ─────────────────────────────────────────────────────────
async def guard_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_chat.type == "private":
        return

    await maybe_announce(context.bot, update.effective_chat.id)
    chat_id = str(update.effective_chat.id)
    uid     = str(update.effective_user.id)

    for session in data["sessions"].get(chat_id, {}).values():
        if session.get("state") != "active":
            continue
        if uid not in session.get("participants", {}):
            continue
        if uid in session.get("breaks", {}):
            continue
        # الأدمن مش بنمسح رسايله
        if await is_admin(context.bot, update.effective_chat.id, int(uid)):
            break
        try:
            await update.message.delete()
            add_points(uid, -POINTS_PENALTY)
            save_data(data)
            pts = get_stats(uid).get("points", 0)
            await context.bot.send_message(
                update.effective_chat.id,
                f"{mention(uid, update.effective_user.first_name)} — {random.choice(DELETE_MESSAGES)}\n"
                f"_خصم {POINTS_PENALTY} نقاط — نقاطك: {pts}_",
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError:
            pass
        break

# ── Scheduled leaderboards ─────────────────────────────────────────────────
async def send_daily_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    for uid in data["stats"]:
        data["stats"][uid]["daily_minutes"] = 0
    save_data(data)
    for cid in list(data["sessions"]):
        try:
            await context.bot.send_message(int(cid), build_leaderboard_text("🏆 ترتيب اليوم"), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"daily lb failed {cid}: {e}")

async def send_weekly_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    for cid in list(data["sessions"]):
        try:
            await context.bot.send_message(int(cid), build_leaderboard_text("🏆 ترتيب الأسبوع", "weekly_points"), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"weekly lb failed {cid}: {e}")
    for uid in data["stats"]:
        data["stats"][uid]["weekly_points"] = 0
        data["stats"][uid]["weekly_minutes"] = 0
    save_data(data)

def schedule_recurring_jobs(app):
    jq    = app.job_queue
    cn    = now()
    mid   = cn.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    jq.run_repeating(send_daily_leaderboard, interval=86400, first=(mid - cn).total_seconds(), name="daily_lb")
    days  = (7 - cn.weekday()) % 7 or 7
    nmon  = cn.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days)
    jq.run_repeating(send_weekly_leaderboard, interval=604800, first=(nmon - cn).total_seconds(), name="weekly_lb")

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN مش متحدد.")

    app = Application.builder().token(BOT_TOKEN).build()

    for cmd, fn in [
        ("start",       cmd_start),
        ("study",       cmd_study),
        ("pomodoro",    cmd_pomodoro),
        ("join",        cmd_join),
        ("break",       cmd_break),
        ("back",        cmd_back),
        ("end",         cmd_end),
        ("status",      cmd_status),
        ("stats",       cmd_stats),
        ("goal",        cmd_goal),
        ("leaderboard", cmd_leaderboard),
        ("weekly",      cmd_weekly),
        ("badges",      cmd_badges),
        ("reset",       cmd_reset),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(join_callback, pattern=r"^join_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mute_commands))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, guard_messages))

    async def post_init(application):
        schedule_recurring_jobs(application)

    app.post_init = post_init
    logger.info("StudyLock Bot شغال...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
