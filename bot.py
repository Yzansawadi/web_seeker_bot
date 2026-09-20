#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py
------
بوت تيليغرام لجدول مواد جامعة IUST.

نظام التنقّل (مُعاد بناؤه بالكامل):
------------------------------------
بدل تمرير "إلى أين نعود" يدويًا كنص خاص بكل زر (كما كان سابقًا)، يحمل كل
مستخدم الآن "مكدّس شاشات" (session["stack"]): في كل مرة يدخل المستخدم
شاشة أعمق (سنة، قائمة حذف...)، تُحفَظ الشاشة التي كان فيها في أعلى
المكدّس. زر "رجوع" يسحب دائمًا آخر عنصر من المكدّس ويعرضه من جديد --
هذا يعمل بشكل صحيح ومنطقي بغض النظر عن المسار الذي سلكه المستخدم للوصول
إلى هنا (بدل كتابة حالة خاصة لكل تركيبة ممكنة من الشاشات).

بالإضافة لزر "رجوع" (خطوة واحدة للخلف)، يوجد الآن في كل شاشة (عدا
الرئيسية) زر "القائمة الرئيسية" يعيد المستخدم فورًا لشاشة البداية
بضغطة واحدة، دون أن يفقد أي مادة اختارها (لا يُصفَّر شيء عند الضغط
عليه -- التصفير يحدث فقط في مكانيه الأصليين: عرض الجدول، وتوليد الجدول
المثالي، تمامًا كما كان مصمّمًا سابقًا).

هيكلة الشاشات:
1) الشاشة الرئيسية (start): تظهر عند /start. 3 أزرار: تحديث الجدول،
   توليد أفضل جدول، عرض أوقات المواد فقط. هذه هي "جذر" التنقّل.
2) شاشة الاختيار (selection): تظهر بعد اختيار أحد المسارين. تحتوي قائمة
   السنوات، وصندوق المواد المختارة، وزر إنهاء المسار (عرض/توليد الجدول)
   وزر حذف مادة إن وُجد اختيار، وزري رجوع/القائمة الرئيسية.
3) شاشة مواد سنة (year): قائمة مواد سنة معيّنة مع علامة ✓ لما سبق
   اختياره، وأزرار رجوع/القائمة الرئيسية/حذف مادة.
4) شاشة حذف مادة (delete): قائمة بكل المواد المختارة حاليًا، الضغط على
   أي منها يحذفها فورًا ويعيد المستخدم لنفس الشاشة التي جاء منها (تمامًا
   كما لو ضغط "رجوع").

ملاحظة أداء: تُحمَّل بيانات subjects.xlsx و IUST_schedule_full.xlsx مرة
واحدة وتُخزَّن في الذاكرة (انظر schedule_data.load_courses)، ولا تُعاد
قراءتها من القرص إلا إذا تغيّر أحد الملفين فعليًا.

---------------------------------------------------------------------------
ملاحظات موثوقية الـ webhook (أُضيفت بعد تشخيص انقطاعات متكرّرة فعلية):
---------------------------------------------------------------------------
اجتمعت ثلاث مشاكل منفصلة كانت تُسبّب توقّف البوت عن الاستجابة رغم أن
Render وUptimeRobot كانا يُظهران أن الخدمة "تعمل بشكل طبيعي":

1) `allowed_updates` كانت تفقد `callback_query` (نوع كل تحديثات الأزرار،
   أي كل تنقّلات البوت تقريبًا) لأن الكود القديم كان يستدعي setWebhook
   بدون تحديد allowed_updates إطلاقًا. توثيق تيليغرام الرسمي ينص على أنه
   "إن لم تُحدَّد، تُستخدَم القيمة السابقة المسجّلة لدى تيليغرام" -- فأي
   ضبط يدوي/تجريبي قديم نسي تضمين callback_query كان يبقى عالقًا للأبد
   لأن الكود لم يكن يصحّحه أبدًا. الحل: تحديدها صراحةً في كل استدعاء.

2) خطأ "520" من طبقة Render الأمامية (يعني: لم يصل أي رد من عمليتنا
   إطلاقًا) كان يحدث أحيانًا لأن الكود القديم كان يستدعي setWebhook
   (أي يخبر تيليغرام "ابدأ الإرسال الآن") *قبل* أن يبدأ uvicorn فعليًا
   بالاستماع على المنفذ. أي طلب يصل تيليغرام في تلك الفجوة الزمنية
   القصيرة يُرفَض فورًا (connection refused) لأنه لا أحد يستمع بعد. الحل:
   ننتظر فعليًا حتى يصبح uvicorn جاهزًا (webserver.started) قبل استدعاء
   setWebhook.

3) توثيق تيليغرام ينص أيضًا: "في حال فشل عدة محاولات تسليم متتالية، نتوقف
   عن المحاولة حتى تُستدعى setWebhook من جديد". أي انقطاع عابر حقيقي
   (إعادة تشغيل حاوية Render لأي سبب) قد يُدخل تيليغرام في هذه الحالة،
   ولن يُصلحها إلا استدعاء setWebhook يدويًا -- إلى أن أضفنا مهمة خلفية
   تُعيد الاستدعاء تلقائيًا كل 20 دقيقة طوال عمر العملية، فتُصلح نفسها
   ذاتيًا دون أي تدخل يدوي بعد الآن.

ملاحظة مهمة: هذه الإصلاحات لا تُلغي احتمال "الإقلاع البارد" (cold start)
نفسه على خطة Render المجانية عند توقّف الخدمة فعليًا لفترة طويلة بلا أي
طلب (Spin down) -- فتلك فجوة حقيقية لا يوجد أحد يستمع خلالها إطلاقًا، بغض
النظر عن ترتيب الكود. لكنها تُلغي كل الحالات التي كانت مشكلتنا نحن تحديدًا
(سباق زمني ذاتي، وضبط ناقص، وحالة "استسلام" تيليغرام)، وتجعل النظام يتعافى
تلقائيًا خلال دقائق معدودة كحد أقصى بدل البقاء معطوبًا حتى تدخل يدوي.

التشغيل محليًا (Polling):
    pip install -r requirements.txt
    python3 bot.py

التشغيل على استضافة سحابية (Webhook):
    راجع ملف DEPLOY_AR.md.
"""

import os
import time
import logging
import subprocess
import sys
import asyncio
from datetime import datetime, timezone

for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name)
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,  # --- تمت الإضافة ---
    filters          # --- تمت الإضافة ---
)

import schedule_data as sd
import pdf_export
import notifier
import schedule_optimizer as opt

try:
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
except ImportError:
    uvicorn = None
    Starlette = None
    PlainTextResponse = None
    Route = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# الإعدادات
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("IUST_BOT_TOKEN", "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXTRACT_SCRIPT_PATH = os.path.join(BASE_DIR, "extract_schedule.py")
TEMP_DIR = os.path.join(BASE_DIR, "temp_pdfs")

UPDATE_COOLDOWN_SECONDS = 2 * 60 * 60  # ساعتان
_last_update_ts = {"value": 0.0}

EXTRACT_SCRIPT_TIMEOUT_SECONDS = 180

WEBHOOK_ALLOWED_UPDATES = ["message", "callback_query"]
WEBHOOK_SELF_HEAL_INTERVAL_SECONDS = 20 * 60

YEAR_NAMES = {
    1: "السنة الأولى",
    2: "السنة الثانية",
    3: "السنة الثالثة",
    4: "السنة الرابعة",
    5: "السنة الخامسة",
}

# --- إعدادات وضع الصيانة (تمت الإضافة) ---
MAINTENANCE_MODE = False
SECRET_CODE = "IUST_ADMIN_99"  # الرمز السري الذي يجب إرساله للبوت لتفعيل الصيانة
# -----------------------------------------

# ---------------------------------------------------------------------------
# حالة كل مستخدم (في الذاكرة فقط، تُفقد عند إعادة تشغيل البوت)
# ---------------------------------------------------------------------------
user_sessions = {}
seen_users = set()
_bot_start_time = datetime.now(timezone.utc)


def track_user(user):
    is_new = user.id not in seen_users
    seen_users.add(user.id)
    if is_new:
        name = user.full_name or user.username or str(user.id)
        logger.info("مستخدم جديد بدأ استخدام البوت: %s (المعرف: %s) | إجمالي المستخدمين منذ آخر تشغيل: %s",
                    name, user.id, len(seen_users))
        asyncio.create_task(
            asyncio.to_thread(notifier.register_new_user, user.id, user.username)
        )


def get_session(user_id):
    s = user_sessions.setdefault(user_id, {"selected": [], "mode": None, "stack": []})
    s.setdefault("mode", None)
    s.setdefault("stack", [])
    return s


def reset_session(user_id):
    user_sessions[user_id] = {"selected": [], "mode": None, "stack": []}
    return user_sessions[user_id]


# ---------------------------------------------------------------------------
# مكدّس التنقّل
# ---------------------------------------------------------------------------

SCREEN_START = ("start",)


def screen_selection(mode):
    return ("selection", mode)


def screen_year(year):
    return ("year", year)


def push_screen(session, descriptor):
    session.setdefault("stack", []).append(descriptor)


def pop_screen(session):
    stack = session.setdefault("stack", [])
    if stack:
        return stack.pop()
    return SCREEN_START


async def render_screen(query, context, user_id, descriptor):
    session = get_session(user_id)
    kind = descriptor[0]

    if kind == "selection":
        years_data = sd.load_courses()
        mode = descriptor[1]
        session["mode"] = mode
        text, keyboard = selection_text_and_keyboard(years_data, mode, session["selected"])
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if kind == "year":
        await show_year_courses(query, context, descriptor[1])
        return

    text, keyboard = start_text_and_keyboard()
    await query.edit_message_text(text, reply_markup=keyboard)


async def go_back(query, context, user_id):
    session = get_session(user_id)
    descriptor = pop_screen(session)
    await render_screen(query, context, user_id, descriptor)


async def go_start(query, context, user_id):
    session = get_session(user_id)
    session["stack"] = []
    text, keyboard = start_text_and_keyboard()
    await query.edit_message_text(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# أدوات مساعدة
# ---------------------------------------------------------------------------

def schedule_file_exists():
    return os.path.exists(sd.SCHEDULE_PATH)


def cooldown_remaining_seconds():
    elapsed = time.time() - _last_update_ts["value"]
    remaining = UPDATE_COOLDOWN_SECONDS - elapsed
    return max(0, int(remaining))


def format_remaining(seconds):
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h > 0:
        return f"{h} ساعة و {m} دقيقة"
    return f"{m} دقيقة"


def selected_courses_block(years_data, selected_list):
    if not selected_list:
        return ""
    lines = ["المواد المختارة حتى الآن:"]
    for i, (year, code) in enumerate(selected_list, start=1):
        course = sd.get_course(years_data, year, code)
        name = course["name"] if course else code
        lines.append(f"{i}. {name}")
    return "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------------------
# بناء لوحات الأزرار
# ---------------------------------------------------------------------------

def nav_row():
    return [
        InlineKeyboardButton("رجوع", callback_data="back"),
        InlineKeyboardButton("القائمة الرئيسية", callback_data="go_start"),
    ]


def build_start_keyboard():
    rows = []
    remaining = cooldown_remaining_seconds()
    if remaining <= 0:
        rows.append([InlineKeyboardButton("تحديث أوقات الجدول", callback_data="update_schedule")])
    else:
        rows.append([InlineKeyboardButton(
            f"التحديث متاح بعد {format_remaining(remaining)}", callback_data="update_cooldown"
        )])
    rows.append([InlineKeyboardButton("توليد أفضل جدول ممكن", callback_data="mode:optimize")])
    rows.append([InlineKeyboardButton("عرض أوقات المواد فقط", callback_data="mode:show")])
    return InlineKeyboardMarkup(rows)


def build_selection_keyboard(years_data, mode, has_selection):
    rows = []

    if mode == "show" and not has_selection:
        rows.append([InlineKeyboardButton(
            "إرسال أوقات جميع المواد (PDF)", callback_data="send_all_times_pdf"
        )])

    year_buttons = []
    for y in sd.get_years(years_data):
        label = YEAR_NAMES.get(y, f"السنة {y}")
        year_buttons.append(InlineKeyboardButton(label, callback_data=f"year:{y}"))
    for i in range(0, len(year_buttons), 2):
        rows.append(year_buttons[i:i + 2])

    if has_selection:
        if mode == "show":
            rows.append([InlineKeyboardButton("عرض الجدول", callback_data="show_schedule")])
        elif mode == "optimize":
            rows.append([InlineKeyboardButton("توليد الجدول المثالي", callback_data="optimize_schedule")])
        rows.append([InlineKeyboardButton("حذف مادة", callback_data="delete_menu:home")])

    rows.append(nav_row())
    return InlineKeyboardMarkup(rows)


def build_delete_keyboard(years_data, selected_list):
    rows = []
    for year, code in selected_list:
        course = sd.get_course(years_data, year, code)
        name = course["name"] if course else code
        rows.append([InlineKeyboardButton(name, callback_data=f"delete_course:{year}:{code}")])

    rows.append(nav_row())
    return InlineKeyboardMarkup(rows)


def build_year_courses_keyboard(years_data, year, selected_codes, has_selection):
    rows = []
    courses = sd.get_courses_for_year(years_data, year)
    for c in courses:
        mark = "✓ " if c["code"] in selected_codes else ""
        label = f"{mark}{c['name']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"course:{year}:{c['code']}")])

    if has_selection:
        rows.append([InlineKeyboardButton("حذف مادة", callback_data=f"delete_menu:year-{year}")])

    rows.append(nav_row())
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# أدوات تنسيق النصوص
# ---------------------------------------------------------------------------

def format_course_info(course):
    lines = [f"{course['name']}", f"الرمز: {course['code']}", ""]

    if not course["sessions"]:
        lines.append("لا تتوفر معلومات جدول لهذه المادة حتى الآن.")
        return "\n".join(lines)

    for s in course["sessions"]:
        room = f" | القاعة: {s['room']}" if s["room"] else ""
        teacher = f" | {s['teacher']}" if s["teacher"] else ""
        lines.append(
            f"- {s['day']} — {s['activity']}\n"
            f"   {s['start']} – {s['end']}{room}{teacher}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# بناء نص ولوحة الشاشة الرئيسية / شاشة الاختيار
# ---------------------------------------------------------------------------

def start_text_and_keyboard(intro_note=""):
    text = intro_note or "اختر ما تريد القيام به:"
    return text, build_start_keyboard()


def selection_text_and_keyboard(years_data, mode, selected_list, intro_note=""):
    has_selection = len(selected_list) > 0
    text = intro_note
    text += selected_courses_block(years_data, selected_list)

    if not schedule_file_exists():
        text += "لم يتم جلب بيانات الجدول حتى الآن. اضغط 'القائمة الرئيسية' ثم 'تحديث أوقات الجدول'."
    else:
        if mode == "optimize":
            text += "اختر المواد التي تريد توليد جدول منها"
        else:
            text += "اختر المواد التي تريد عرض أوقاتها."

    keyboard = build_selection_keyboard(years_data, mode, has_selection)
    return text, keyboard


# ---------------------------------------------------------------------------
# المعالجات (Handlers)
# ---------------------------------------------------------------------------

# --- دالة تفعيل/إيقاف وضع الصيانة (تمت الإضافة) ---
async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MAINTENANCE_MODE
    
    if update.message and update.message.text == SECRET_CODE:
        MAINTENANCE_MODE = not MAINTENANCE_MODE
        
        status = "مُفَعّل 🔴 (المستخدمون سيرون رسالة الصيانة ولن تعمل الأوامر)" if MAINTENANCE_MODE else "مُعَطّل 🟢 (البوت يعمل بشكل طبيعي)"
        await update.message.reply_text(f"⚙️ تم تغيير وضع الصيانة.\nالوضع الحالي: {status}")
# -------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # --- التحقق من وضع الصيانة (تمت الإضافة) ---
    if MAINTENANCE_MODE:
        await update.message.reply_text("عذراً، البوت حالياً تحت الصيانة الدورية ⚙️ ولن يستجيب للأوامر. يرجى المحاولة لاحقاً.")
        return
    # -----------------------------------------

    user_id = update.effective_user.id
    track_user(update.effective_user)
    reset_session(user_id)
    text, keyboard = start_text_and_keyboard(intro_note=" اهلا بك في بوت websseker. \n\n")
    await update.message.reply_text(text, reply_markup=keyboard)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = datetime.now(timezone.utc) - _bot_start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)

    active_sessions = sum(1 for s in user_sessions.values() if s["selected"])

    text = (
        "إحصائيات البوت (منذ آخر تشغيل):\n\n"
        f"عدد المستخدمين الفريدين: {len(seen_users)}\n"
        f"عدد المستخدمين الذين لديهم اختيار غير مكتمل حاليًا: {active_sessions}\n"
        f"مدة التشغيل الحالية: {hours} ساعة و {minutes} دقيقة\n\n"
        "ملاحظة: هذا العدد يبدأ من الصفر مع كل إعادة تشغيل للبوت."
    )
    await update.message.reply_text(text)


async def show_year_courses(query, context, year):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()

    selected_codes = {code for (y, code) in session["selected"] if y == year}
    has_selection = len(session["selected"]) > 0

    year_label = YEAR_NAMES.get(year, f"السنة {year}")
    text = f"مواد {year_label}\n\nاختر مادة:"
    await query.edit_message_text(
        text,
        reply_markup=build_year_courses_keyboard(years_data, year, selected_codes, has_selection),
    )


async def select_course(query, context, year, code):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()
    course = sd.get_course(years_data, year, code)

    if course is None:
        await query.edit_message_text("هذه المادة غير موجودة، قد تكون البيانات تغيّرت بعد آخر تحديث.")
        return

    if (year, code) not in session["selected"]:
        session["selected"].append((year, code))

    await go_back(query, context, user_id)


async def show_delete_menu(query, context):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()

    if not session["selected"]:
        await go_back(query, context, user_id)
        return

    text = "اختر المادة التي تريد حذفها من قائمة اختياراتك، أو اضغط رجوع للعودة بدون حذف:"
    keyboard = build_delete_keyboard(years_data, session["selected"])
    await query.edit_message_text(text, reply_markup=keyboard)


async def delete_course(query, context, year, code):
    user_id = query.from_user.id
    session = get_session(user_id)
    session["selected"] = [
        (y, c) for (y, c) in session["selected"] if not (y == year and c == code)
    ]
    await go_back(query, context, user_id)


async def show_schedule(query, context):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()

    selected_snapshot = list(session["selected"])

    if not selected_snapshot:
        await query.edit_message_text("لم تقم باختيار أي مادة حتى الآن.")
        reset_session(user_id)
        start_text, start_kb = start_text_and_keyboard()
        await context.bot.send_message(
            chat_id=query.message.chat_id, text=start_text, reply_markup=start_kb
        )
        return

    await query.edit_message_text("جاري تحضير ملف الجدول...\nيرجى الانتظار قليلاً.")

    os.makedirs(TEMP_DIR, exist_ok=True)
    pdf_path = os.path.join(TEMP_DIR, f"schedule_{user_id}.pdf")
    try:
        pdf_export.build_schedule_pdf(years_data, selected_snapshot, pdf_path)
        with open(pdf_path, "rb") as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                filename="times_schedule.pdf",
            )
    except Exception:
        logger.exception("فشل إنشاء أو إرسال ملف PDF للجدول")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="تعذّر إنشاء ملف PDF للجدول. حاول مجددًا لاحقًا.",
        )
    finally:
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except OSError:
                pass

    reset_session(user_id)
    start_text, start_kb = start_text_and_keyboard()
    await context.bot.send_message(
        chat_id=query.message.chat_id, text=start_text, reply_markup=start_kb
    )


async def run_update_schedule(query, context):
    remaining = cooldown_remaining_seconds()
    if remaining > 0:
        await query.answer(
            f"يرجى الانتظار {format_remaining(remaining)} قبل إعادة التحديث.", show_alert=True
        )
        return

    user_id = query.from_user.id
    had_data_before = schedule_file_exists()

    await query.answer("بدأ التحديث، قد يستغرق هذا دقيقة...")
    await query.edit_message_text("جاري جلب أحدث الأوقات من موقع الجامعة...\nيرجى الانتظار.")

    error_snippet = ""
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        result = subprocess.run(
            [sys.executable, "-X", "utf8", EXTRACT_SCRIPT_PATH],
            cwd=BASE_DIR,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=EXTRACT_SCRIPT_TIMEOUT_SECONDS,
        )
        success = result.returncode == 0 and schedule_file_exists()
        if not success:
            error_snippet = (result.stderr or result.stdout or "").strip()[-300:]
            logger.error("فشل extract_schedule.py: %s", result.stderr[-2000:])
    except subprocess.TimeoutExpired:
        success = False
        error_snippet = f"انتهت المهلة بعد {EXTRACT_SCRIPT_TIMEOUT_SECONDS} ثانية."
        logger.error("انتهت مهلة extract_schedule.py بعد %s ثانية", EXTRACT_SCRIPT_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        success = False
        error_snippet = str(exc)[-300:]
        logger.exception("خطأ غير متوقع أثناء تشغيل extract_schedule.py")

    _last_update_ts["value"] = time.time()
    sd.load_courses(force_reload=True)
    asyncio.create_task(asyncio.to_thread(notifier.notify_update_result, success, error_snippet))

    if success:
        note = "تم تحديث الجدول بنجاح بأحدث البيانات من موقع الجامعة.\n\n"
    elif had_data_before:
        note = "لم يكتمل التحديث بنجاح. سيتم الاستمرار باستخدام البيانات من آخر تحديث ناجح.\n\n"
    else:
        note = (
            "فشل التحديث ولا توجد بيانات جدول متاحة حتى الآن.\n"
            "هذا يعني عادة أن السكربت لم يتمكن من الوصول إلى موقع الجامعة، "
            "أو أن بنية الموقع قد تغيّرت.\n"
        )
        if error_snippet:
            note += f"تفاصيل: {error_snippet}\n"
        note += "\n"

    reset_session(user_id)
    start_text, start_kb = start_text_and_keyboard(intro_note=note)
    await query.edit_message_text(start_text, reply_markup=start_kb)


def build_optimized_status_text(result):
    if result["timed_out"] and not result["schedules"]:
        return (
            "انتهى وقت المعالجة قبل إيجاد جدول مثالي.\n"
            "جرّب اختيار عدد أقل من المواد للحصول على نتيجة أسرع."
        )

    no_data = result.get("no_data_courses", [])
    if no_data:
        return (
            "لا توجد معلومات جدول كافية لإنشاء جدول مثالي.\n"
            "المواد التالية بدون معلومات أوقات: " + "، ".join(no_data)
        )
    return "لم يتمكن النظام من إيجاد أي جدول ممكن للمواد المختارة."


def build_optimized_summary_text(result):
    best = result["schedules"][0]
    lines = ["تم إنشاء الجدول المثالي بنجاح."]
    lines.append(f"عدد أيام الحضور: {best['days_count']}")

    if best["total_gap_minutes"] == 0:
        lines.append("لا توجد فراغات بين المحاضرات في أي يوم.")
    else:
        lines.append(f"مجموع الفراغات بين المحاضرات: {best['total_gap_minutes']} دقيقة.")

    if result["excluded_courses"]:
        excluded_names = "، ".join(e["name"] for e in result["excluded_courses"])
        lines.append(f"تعذّر تضمين المواد التالية بسبب تعارض حتمي: {excluded_names}")

    if result.get("no_data_courses"):
        lines.append(
            "مواد بدون معلومات أوقات (لم تُدرَج في الجدول): "
            + "، ".join(result["no_data_courses"])
        )

    lines.append("التفاصيل الكاملة مرفقة في ملف PDF أدناه.")
    return "\n".join(lines)


async def send_all_times_pdf(query, context):
    await query.answer()
    await query.edit_message_text("جاري تجميع أوقات جميع المواد في ملف PDF...\nقد يستغرق هذا لحظة.")

    years_data = sd.load_courses()
    user_id = query.from_user.id
    os.makedirs(TEMP_DIR, exist_ok=True)
    pdf_path = os.path.join(TEMP_DIR, f"all_times_{user_id}.pdf")

    try:
        await asyncio.to_thread(pdf_export.build_all_times_pdf, years_data, pdf_path)
        with open(pdf_path, "rb") as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                filename="all_course_times.pdf",
                caption="أوقات جميع المواد الدراسية",
            )
    except Exception:
        logger.exception("فشل إنشاء أو إرسال PDF جميع الأوقات")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="تعذّر إنشاء الملف. تأكد من أن بيانات الجدول محدّثة (اضغط تحديث أوقات الجدول).",
        )
    finally:
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except OSError:
                pass

    session = get_session(user_id)
    text, keyboard = selection_text_and_keyboard(years_data, session.get("mode", "show"), session["selected"])
    await context.bot.send_message(
        chat_id=query.message.chat_id, text=text, reply_markup=keyboard
    )


async def optimize_schedule(query, context):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()

    if not session["selected"]:
        await query.answer("لا توجد مواد مختارة.", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(
        "جاري تحليل الجدول المثالي...\n"
        "قد يستغرق هذا بضع ثوانٍ حسب عدد المواد المختارة."
    )

    selected_snapshot = list(session["selected"])

    try:
        result = await asyncio.to_thread(
            opt.find_best_schedules,
            years_data, selected_snapshot,
            sd.get_course, sd.time_to_minutes,
            top_n=1, time_budget_seconds=8.0,
        )
    except Exception:
        logger.exception("خطأ في محرك التحسين")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="حدث خطأ غير متوقع أثناء توليد الجدول. حاول مجددًا أو اختر مواد مختلفة.",
        )
        reset_session(user_id)
        start_text, start_kb = start_text_and_keyboard()
        await context.bot.send_message(chat_id=query.message.chat_id, text=start_text, reply_markup=start_kb)
        return

    if result["schedules"]:
        result_text = build_optimized_summary_text(result)
    else:
        result_text = build_optimized_status_text(result)
    await context.bot.send_message(chat_id=query.message.chat_id, text=result_text)

    if result["schedules"]:
        best = result["schedules"][0]
        os.makedirs(TEMP_DIR, exist_ok=True)
        pdf_path = os.path.join(TEMP_DIR, f"optimal_{user_id}.pdf")
        try:
            stats_lines = [f"عدد أيام الحضور: {best['days_count']}  |  مجموع الفراغات: {best['total_gap_minutes']} دقيقة"]
            if result["excluded_courses"]:
                excl = "، ".join(e["name"] for e in result["excluded_courses"])
                stats_lines.append(f"مواد مستثناة: {excl}")
            pdf_export.build_optimized_schedule_pdf(best["options"], pdf_path, stats_lines=stats_lines)
            with open(pdf_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    filename="webseeker_schedule.pdf",
                    caption="الجدول المثالي بصيغة PDF",
                )
        except Exception:
            logger.exception("فشل إنشاء أو إرسال PDF الجدول المثالي")
        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass

    reset_session(user_id)
    start_text, start_kb = start_text_and_keyboard()
    await context.bot.send_message(chat_id=query.message.chat_id, text=start_text, reply_markup=start_kb)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # --- التحقق من وضع الصيانة (تمت الإضافة) ---
    if MAINTENANCE_MODE:
        # يظهر نافذة منبثقة للمستخدم تبلغه بالصيانة
        await query.answer("عذراً، البوت حالياً تحت الصيانة الدورية ⚙️", show_alert=True)
        return
    # -----------------------------------------

    data = query.data
    track_user(query.from_user)
    logger.info("ضغطة زر من المستخدم %s: %s", query.from_user.id, data)

    user_id = query.from_user.id
    username = query.from_user.username
    session = get_session(user_id)

    def _fire_log_event(event_type, value=""):
        asyncio.create_task(asyncio.to_thread(notifier.log_event, user_id, username, event_type, value))

    if data == "back" or data == "go_start":
        _fire_log_event("back_button")
    elif data.startswith("year:"):
        _fire_log_event("select_year", data.split(":", 1)[1])
    elif data.startswith("course:"):
        _, year_str, code = data.split(":", 2)
        years_data_for_log = sd.load_courses()
        course_for_log = sd.get_course(years_data_for_log, int(year_str), code)
        course_name = course_for_log["name"] if course_for_log else code
        _fire_log_event("select_subject", course_name)
    elif data.startswith("delete_course:"):
        _fire_log_event("delete_subject")
    elif data == "show_schedule":
        _fire_log_event("show_schedule")

    if data == "update_cooldown":
        remaining = cooldown_remaining_seconds()
        await query.answer(
            f"تم استخدام التحديث مؤخرًا. حاول مرة أخرى بعد {format_remaining(remaining)}.",
            show_alert=True,
        )
        return

    if data == "update_schedule":
        await run_update_schedule(query, context)
        return

    if data.startswith("mode:"):
        await query.answer()
        chosen_mode = data.split(":", 1)[1]
        push_screen(session, SCREEN_START)
        session["mode"] = chosen_mode
        years_data = sd.load_courses()
        text, keyboard = selection_text_and_keyboard(years_data, chosen_mode, session["selected"])
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if data == "send_all_times_pdf":
        await send_all_times_pdf(query, context)
        return

    if data == "back":
        await query.answer()
        await go_back(query, context, user_id)
        return

    if data == "go_start":
        await query.answer()
        await go_start(query, context, user_id)
        return

    if data.startswith("delete_menu:"):
        await query.answer()
        origin = data.split(":", 1)[1]
        if origin.startswith("year-"):
            push_screen(session, screen_year(int(origin.split("-", 1)[1])))
        else:
            push_screen(session, screen_selection(session.get("mode") or "show"))
        await show_delete_menu(query, context)
        return

    if data.startswith("delete_course:"):
        await query.answer()
        _, year_str, code = data.split(":", 2)
        await delete_course(query, context, int(year_str), code)
        return

    if data.startswith("year:"):
        await query.answer()
        year = int(data.split(":")[1])
        push_screen(session, screen_selection(session.get("mode") or "show"))
        await show_year_courses(query, context, year)
        return

    if data.startswith("course:"):
        await query.answer()
        _, year_str, code = data.split(":", 2)
        await select_course(query, context, int(year_str), code)
        return

    if data == "show_schedule":
        await query.answer()
        await show_schedule(query, context)
        return

    if data == "optimize_schedule":
        _fire_log_event("optimize_schedule")
        await optimize_schedule(query, context)
        return

    await query.answer()


async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("استثناء غير متوقّع أثناء معالجة تحديث %s", update, exc_info=context.error)


async def healthcheck(request):
    return PlainTextResponse("OK")


async def run_webhook_server(app):
    port = int(os.environ.get("PORT", "10000"))
    external_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    webhook_path = "webhook"
    webhook_url = f"{external_url.rstrip('/')}/{webhook_path}"
    secret_token = os.environ.get("WEBHOOK_SECRET") or None

    async def telegram_webhook(request):
        if secret_token:
            incoming_token = request.headers.get("x-telegram-bot-api-secret-token")
            if incoming_token != secret_token:
                logger.warning("طلب webhook مرفوض: رأس السرّية غير مطابق أو مفقود.")
                return PlainTextResponse("Forbidden", status_code=403)

        data = await request.json()
        update = Update.de_json(data=data, bot=app.bot)
        await app.update_queue.put(update)
        return PlainTextResponse("OK")

    starlette_app = Starlette(
        routes=[
            Route(f"/{webhook_path}", telegram_webhook, methods=["POST"]),
            Route("/healthcheck", healthcheck, methods=["GET"]),
            Route("/", healthcheck, methods=["GET"]),
        ]
    )

    webserver = uvicorn.Server(
        config=uvicorn.Config(
            app=starlette_app,
            port=port,
            host="0.0.0.0",
            log_level="info",
        )
    )

    logger.info("بدء تشغيل البوت بوضع Webhook على المنفذ %s ...", port)
    logger.info("عنوان الـ Webhook: %s", webhook_url)

    async def _set_webhook_with_retries(*, drop_pending_updates):
        last_exc = None
        for attempt in range(1, 4):
            try:
                await app.bot.set_webhook(
                    url=webhook_url,
                    secret_token=secret_token,
                    drop_pending_updates=drop_pending_updates,
                    allowed_updates=WEBHOOK_ALLOWED_UPDATES,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.exception("فشلت محاولة %s من %s لضبط الـ webhook", attempt, 3)
                if attempt < 3:
                    await asyncio.sleep(2 * attempt)
        raise last_exc

    async def _refresh_webhook_periodically():
        while True:
            await asyncio.sleep(WEBHOOK_SELF_HEAL_INTERVAL_SECONDS)
            try:
                await app.bot.set_webhook(
                    url=webhook_url,
                    secret_token=secret_token,
                    allowed_updates=WEBHOOK_ALLOWED_UPDATES,
                )
                logger.info("تم تحديث تسجيل الـ webhook الدوري بنجاح.")
            except Exception:
                logger.exception(
                    "فشل التحديث الدوري لتسجيل الـ webhook (ستُعاد المحاولة بعد %s ثانية)",
                    WEBHOOK_SELF_HEAL_INTERVAL_SECONDS,
                )

    async with app:
        await app.start()

        server_task = asyncio.create_task(webserver.serve())
        while not webserver.started:
            await asyncio.sleep(0.05)

        await _set_webhook_with_retries(drop_pending_updates=True)
        asyncio.create_task(_refresh_webhook_periodically())

        try:
            await server_task
        finally:
            await app.stop()


def main():
    if BOT_TOKEN == "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print(
            "خطأ: يجب ضبط توكن البوت أولًا.\n"
            "عدّل قيمة BOT_TOKEN في أعلى bot.py، أو شغّل البوت بهذا الشكل:\n"
            "  IUST_BOT_TOKEN=123456:ABC-your-token python3 bot.py"
        )
        return

    external_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")

    if external_url:
        if uvicorn is None:
            print(
                "خطأ: وضع Webhook يحتاج مكتبتي uvicorn و starlette.\n"
                "ثبّتهما عبر: pip install -r requirements.txt"
            )
            return
        app = Application.builder().token(BOT_TOKEN).updater(None).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats))
        
        # --- إضافة معالج الصيانة هنا (يعمل للرسائل العادية) ---
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, toggle_maintenance))
        # ---------------------------------------------------
        
        app.add_handler(CallbackQueryHandler(button_handler))
        app.add_error_handler(error_handler)
        asyncio.run(run_webhook_server(app))
    else:
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats))
        
        # --- إضافة معالج الصيانة هنا (يعمل للرسائل العادية) ---
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, toggle_maintenance))
        # ---------------------------------------------------
        
        app.add_handler(CallbackQueryHandler(button_handler))
        app.add_error_handler(error_handler)
        logger.info("بدء تشغيل البوت (long polling)...")
        app.run_polling()


if __name__ == "__main__":
    main()