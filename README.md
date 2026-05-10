# 📚 StudyLock Bot

> بوت تيليجرام بيساعد مجموعات المذاكرة على التركيز — بيقفل الشات أثناء الجلسات ويتتبع إحصائيات كل عضو.

---

## ✨ المميزات

- 🔒 **قفل الشات تلقائياً** أثناء جلسة المذاكرة
- 🗑️ **حذف رسايل** أي مشترك يكتب أثناء الجلسة
- 🍅 **وضع بوموڊورو** — دورات 25 دقيقة مذاكرة + 5 دقايق راحة
- ☕ **استراحات شخصية** لكل عضو مع مؤقت تلقائي
- 📊 **إحصائيات** — إجمالي وقت المذاكرة، عدد الجلسات، الـ streak
- 🏆 **Leaderboard** — ترتيب أعضاء المجموعة
- 💾 **SQLite** — الداتا محفوظة على الديسك ومش بتطير عند restart

---

## الأوامر

| الأمر | الوصف |
|-------|-------|
| `/study 2h` | ابدأ جلسة ساعتين |
| `/study 90m` | ابدأ جلسة 90 دقيقة |
| `/study 1h30m` | ابدأ جلسة ساعة ونص |
| `/pomodoro` | جلسة بوموڊورو (4 دورات افتراضياً) |
| `/pomodoro 6` | جلسة بوموڊورو 6 دورات |
| `/break 10` | خد استراحة شخصية 10 دقايق |
| `/back` | ارجع من الاستراحة بدري |
| `/status` | شوف حالة الجلسة الحالية |
| `/stats` | إحصائياتك الشخصية |
| `/leaderboard` | ترتيب أعضاء المجموعة |
| `/end` | اخرج من الجلسة |
| `/cancel` | إلغاء الجلسة *(للأدمن أو اللي بدأها فقط)* |

---

## 🚀 طريقة التشغيل

### 1. إنشاء البوت

1. افتح تيليجرام وابحث عن **[@BotFather](https://t.me/BotFather)**
2. ابعت `/newbot` واتبع التعليمات
3. احتفظ بالـ **Token** اللي هيديهولك

### 2. تثبيت المتطلبات

```bash
pip install -r requirements.txt
```

> متطلبات: Python 3.11 أو أحدث

### 3. تحديد الـ Token

**على Linux/Mac:**
```bash
export BOT_TOKEN="ضع_التوكن_هنا"
python bot.py
```

**على Windows:**
```cmd
set BOT_TOKEN=ضع_التوكن_هنا
python bot.py
```

**أو أنشئ ملف `.env`** (لا يُرفع على GitHub):
```
BOT_TOKEN=ضع_التوكن_هنا
```

### 4. إضافة البوت للجروب

1. أضف البوت للجروب
2. اعمله **أدمن** وفعّل الصلاحيات دي:
   - ✅ **Delete Messages** — عشان يمسح رسايل المذاكرين
   - ✅ **Restrict Members** — عشان يقفل/يفتح الشات

---

## ☁️ الرفع على Railway أو Render

### Railway

1. ارفع الكود على GitHub
2. افتح [railway.app](https://railway.app) وعمل **New Project من GitHub**
3. في **Variables** حط:
   ```
   BOT_TOKEN=ضع_التوكن_هنا
   DATA_DIR=/data
   ```
4. في **Volumes** أنشئ volume على المسار `/data` عشان الداتا ماتطيرش

### Render

1. ارفع الكود على GitHub
2. افتح [render.com](https://render.com) وعمل **New Background Worker**
3. في **Environment Variables** حط:
   ```
   BOT_TOKEN=ضع_التوكن_هنا
   DATA_DIR=/data
   ```
4. في **Disks** أنشئ disk على المسار `/data`

> ⚠️ لو ما حددتش `DATA_DIR`، الداتا بتتحفظ في `/tmp` وبتطير عند كل restart.

---

## 💾 الداتا والتخزين

البوت بيستخدم **SQLite** — الملف بيتحفظ في:
- `$DATA_DIR/studybot.db` لو حددت `DATA_DIR`
- `/tmp/studybot.db` افتراضياً

### نقل الداتا القديمة (Migration)

لو كنت بتستخدم النسخة القديمة اللي بتحفظ في JSON، شغّل الأمر ده مرة واحدة بس:

```bash
python migrate.py /tmp/studybot_data.json /tmp/studybot.db
```

أو لو عندك persistent volume:
```bash
python migrate.py /tmp/studybot_data.json /data/studybot.db
```

---

## 📁 هيكل المشروع

```
study-session-bot/
├── bot.py            ← الكود الرئيسي للبوت
├── migrate.py        ← نقل الداتا القديمة لـ SQLite
├── requirements.txt  ← المكتبات المطلوبة
├── Procfile          ← للرفع على Railway/Render
├── .gitignore        ← ملفات يتم تجاهلها
└── README.md         ← هذا الملف
```

---

## 👨‍💻 التطوير

بواسطة **Ahmed AbdelRazek** — [DevCatowa](https://t.me/DevCatowa)
