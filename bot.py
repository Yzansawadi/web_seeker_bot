#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py
------
بوت تيليغرام لجدول مواد جامعة IUST.

هيكلة التنقل (بحسب تصميم المستخدم):

1) الشاشة الرئيسية (تظهر عند /start، وبعد كل اختيار مادة ناجح):
   - زر "تحديث أوقات الجدول".
   - قائمة السنوات (1 إلى 5).
   - إذا كانت هناك مواد محفوظة من قبل: تظهر في صندوق مرقّم فوق السنوات،
     ويظهر زر "عرض الجدول" وزر "حذف مادة" تحت قائمة السنوات.
   - إذا لم تكن هناك أي مادة محفوظة بعد: لا يظهر أي زر إضافي تحت السنوات.

2) شاشة مواد سنة معيّنة (بعد اختيار سنة من الشاشة الرئيسية):
   - قائمة مواد تلك السنة، مع علامة (✓) أمام أي مادة سبق اختيارها منها.
   - تحتها زر "رجوع"، وإن وُجد اختيار سابق يظهر زر "حذف مادة" أيضًا.
   - "رجوع" هنا رجوع حقيقي فقط: يعيد الشاشة الرئيسية دون أي تسجيل جديد من
     هذه الزيارة، **مع الحفاظ الكامل على كل ما تم اختياره سابقًا** (لا
     تصفير هنا أبدًا).
   - اختيار مادة هنا يسجّلها، ثم يعيد عرض الشاشة الرئيسية (حالة 1) محدّثة.

3) "حذف مادة" (متاح في الشاشة الرئيسية وشاشة مواد السنة معًا، بعد أول
   اختيار): يعرض قائمة بكل المواد المختارة حاليًا، والضغط على أي منها
   يحذفها فورًا، ثم يعيد المستخدم إلى الشاشة التي جاء منها (الرئيسية أو
   شاشة مواد سنة معيّنة) بنفس حالتها.

4) "عرض الجدول":
   - يرسل رسالة نصية بالجدول مقسّمًا حسب الأيام، ثم ملف PDF.
   - بعد الإرسال مباشرة، يصفّر كل الاختيارات المحفوظة لتبدأ جلسة جديدة.
   - هذا هو **المكان الوحيد** الذي تُصفَّر فيه الاختيارات تلقائيًا.

ملاحظة أداء: تُحمَّل بيانات subjects.xlsx و IUST_schedule_full.xlsx مرة
واحدة وتُخزَّن في الذاكرة (انظر schedule_data.load_courses)، ولا تُعاد
قراءتها من القرص إلا إذا تغيّر أحد الملفين فعليًا. هذا يجعل استجابة
البوت أسرع بشكل كبير مقارنة بإعادة قراءة وتحليل الملفين في كل ضغطة زر.

التشغيل محليًا (Polling) -- يبقى جهازك يعمل طوال الوقت:
    pip install -r requirements.txt
    python3 bot.py

التشغيل على استضافة سحابية (Webhook) -- لا يحتاج جهازك مفتوحًا:
    يُفعَّل تلقائيًا إذا كان متغيّر البيئة RENDER_EXTERNAL_URL موجودًا
    (تضبطه استضافات مثل Render تلقائيًا)، أو إذا ضبطت WEBHOOK_URL يدويًا.
    راجع ملف DEPLOY_AR.md للشرح الكامل خطوة بخطوة لرفع البوت على Render.
"""

import os
import time
import logging
import subprocess
import sys
import asyncio

# على ويندوز، الترميز الافتراضي لنافذة الأوامر (مثل cp1252) لا يدعم الحروف
# العربية، وهذا يتسبب بانهيار أي print()/logging يحتوي عليها. نفرض هنا
# ترميز UTF-8 على مخرجات البرنامج قبل أي شيء آخر لتفادي هذه المشكلة كليًا.
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
)

import schedule_data as sd
import pdf_export

# uvicorn/starlette مطلوبتان فقط لوضع Webhook (الاستضافة السحابية). إن لم
# تكونا مثبَّتتين والبوت يعمل محليًا بوضع Polling فقط، لا مشكلة في ذلك --
# الاستيراد يتم بأمان هنا، والاستخدام الفعلي مؤجَّل لوقت تفعيل وضع Webhook.
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

# زر "تحديث أوقات الجدول" متاح للجميع، لكن مرة واحدة فقط كل هذه المدة
# (مشتركة بين كل مستخدمي البوت دفعة واحدة).
UPDATE_COOLDOWN_SECONDS = 2 * 60 * 60  # ساعتان
_last_update_ts = {"value": 0.0}

# أقصى مدة انتظار لتشغيل extract_schedule.py قبل اعتباره متعطلًا
EXTRACT_SCRIPT_TIMEOUT_SECONDS = 180

YEAR_NAMES = {
    1: "السنة الأولى",
    2: "السنة الثانية",
    3: "السنة الثالثة",
    4: "السنة الرابعة",
    5: "السنة الخامسة",
}

# ---------------------------------------------------------------------------
# حالة كل مستخدم (في الذاكرة فقط، تُفقد عند إعادة تشغيل البوت)
# ---------------------------------------------------------------------------
# user_id -> {
#     "selected": [ (year, code), ... ]      # المواد المختارة بترتيب اختيارها
# }
user_sessions = {}


def get_session(user_id):
    return user_sessions.setdefault(user_id, {"selected": []})


def reset_session(user_id):
    user_sessions[user_id] = {"selected": []}
    return user_sessions[user_id]


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
    """
    يبني نص صندوق "المواد المختارة" المرقّم الذي يظهر فوق قائمة السنوات
    في الشاشة الرئيسية. يعيد نصًا فارغًا إن لم تكن هناك أي مادة مختارة.
    """
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

def build_home_keyboard(years_data, has_selection):
    rows = []

    remaining = cooldown_remaining_seconds()
    if remaining <= 0:
        rows.append([InlineKeyboardButton("تحديث أوقات الجدول", callback_data="update_schedule")])
    else:
        rows.append([InlineKeyboardButton(
            f"التحديث متاح بعد {format_remaining(remaining)}", callback_data="update_cooldown"
        )])

    year_buttons = []
    for y in sd.get_years(years_data):
        label = YEAR_NAMES.get(y, f"السنة {y}")
        year_buttons.append(InlineKeyboardButton(label, callback_data=f"year:{y}"))
    for i in range(0, len(year_buttons), 2):
        rows.append(year_buttons[i:i + 2])

    # زر "عرض الجدول" و"حذف مادة" يظهران فقط إذا كانت هناك مادة مختارة
    # على الأقل، وبدون أي زر "رجوع" في هذه الشاشة (حسب التصميم المطلوب).
    if has_selection:
        rows.append([InlineKeyboardButton("عرض الجدول", callback_data="show_schedule")])
        rows.append([InlineKeyboardButton("حذف مادة", callback_data="delete_menu:home")])

    return InlineKeyboardMarkup(rows)


def build_delete_keyboard(years_data, selected_list, return_to):
    """
    return_to: نص آمن لا يحتوي ':' -- يكون "home" أو "year-<رقم السنة>".
    يحدد إلى أين نعود بعد الحذف أو عند الضغط على "تراجع"، حتى تبقى تجربة
    المستخدم متّسقة مع الشاشة التي جاء منها.
    """
    rows = []
    for year, code in selected_list:
        course = sd.get_course(years_data, year, code)
        name = course["name"] if course else code
        rows.append([InlineKeyboardButton(name, callback_data=f"delete_course:{year}:{code}:{return_to}")])

    rows.append([InlineKeyboardButton("تراجع", callback_data=f"cancel_delete:{return_to}")])
    return InlineKeyboardMarkup(rows)


def build_year_courses_keyboard(years_data, year, selected_codes, has_selection):
    rows = []
    courses = sd.get_courses_for_year(years_data, year)
    for c in courses:
        mark = "✓ " if c["code"] in selected_codes else ""
        label = f"{mark}{c['name']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"course:{year}:{c['code']}")])

    # "رجوع" هنا رجوع حقيقي فقط: يعيد الشاشة الرئيسية دون أي اختيار جديد
    # من هذه الزيارة، لكن مع الحفاظ الكامل على كل ما تم اختياره سابقًا.
    rows.append([InlineKeyboardButton("رجوع", callback_data="back_home")])

    if has_selection:
        rows.append([InlineKeyboardButton("حذف مادة", callback_data=f"delete_menu:year-{year}")])

    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# أدوات تنسيق النصوص
# ---------------------------------------------------------------------------

def format_course_info(course):
    """نص معلومات المادة: الاسم، ثم كل جلسة (اليوم، النوع، الوقت)."""
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


def build_full_schedule_text(years_data, selected_list):
    """
    selected_list: قائمة (year, code) بترتيب الاختيار.
    يبني نص الجدول النهائي مقسّمًا حسب الأيام ومرتبًا زمنيًا داخل كل يوم.
    """
    if not selected_list:
        return "لم تقم باختيار أي مادة حتى الآن."

    all_sessions = []  # (day, start_min, course_name, session)
    for year, code in selected_list:
        course = sd.get_course(years_data, year, code)
        if not course:
            continue
        for s in course["sessions"]:
            all_sessions.append((s["day"], s["start_min"], course["name"], s))

    if not all_sessions:
        return "لا تتوفر معلومات جدول للمواد التي اخترتها."

    by_day = {}
    for day, start_min, name, s in all_sessions:
        by_day.setdefault(day, []).append((start_min, name, s))

    ordered_days = [d for d in sd.DAY_ORDER if d in by_day]

    lines = ["جدولك الأسبوعي\n"]
    for day in ordered_days:
        lines.append(f"\n{day}")
        sessions_today = sorted(by_day[day], key=lambda x: x[0])
        for _, name, s in sessions_today:
            room = f" | القاعة: {s['room']}" if s["room"] else ""
            teacher = f" | {s['teacher']}" if s["teacher"] else ""
            lines.append(
                f"  {s['start']} – {s['end']}  —  {name}\n"
                f"      {s['activity']}{room}{teacher}"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# بناء نص ولوحة الشاشة الرئيسية (مشترك بين /start وكل مسارات الرجوع إليها)
# ---------------------------------------------------------------------------

def home_text_and_keyboard(years_data, selected_list, intro_note=""):
    has_selection = len(selected_list) > 0
    text = intro_note
    text += selected_courses_block(years_data, selected_list)

    if not schedule_file_exists():
        text += "لم يتم جلب بيانات الجدول حتى الآن. اضغط على زر تحديث أوقات الجدول أولًا، ثم اختر السنة الدراسية."
    else:
        text += "اختر السنة الدراسية للمتابعة."

    keyboard = build_home_keyboard(years_data, has_selection)
    return text, keyboard


# ---------------------------------------------------------------------------
# المعالجات (Handlers)
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_session(user_id)
    years_data = sd.load_courses()
    text, keyboard = home_text_and_keyboard(
        years_data, [], intro_note="أهلًا بك في بوت جدول IUST.\n\n"
    )
    await update.message.reply_text(text, reply_markup=keyboard)


async def go_home(query, context, selected_list, intro_note=""):
    years_data = sd.load_courses()
    text, keyboard = home_text_and_keyboard(years_data, selected_list, intro_note=intro_note)
    await query.edit_message_text(text, reply_markup=keyboard)


async def show_year_courses(query, context, year):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()

    selected_codes = {code for (y, code) in session["selected"] if y == year}
    has_selection = len(session["selected"]) > 0

    year_label = YEAR_NAMES.get(year, f"السنة {year}")
    text = f"مواد {year_label}\n\nاختر مادة لعرض جدولها:"
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

    # بعد اختيار مادة، نعود مباشرة للشاشة الرئيسية (قائمة السنوات) مع تحديث
    # صندوق "المواد المختارة" وإظهار زر "عرض الجدول".
    await go_home(query, context, session["selected"])


async def back_home(query, context):
    """
    زر 'رجوع' من شاشة مواد السنة: يعيد الشاشة الرئيسية فقط، دون أي تسجيل
    جديد من هذه الزيارة، ومع الحفاظ الكامل على كل المواد المختارة سابقًا.
    التصفير الوحيد المسموح به يحدث بعد "عرض الجدول" أو زر "حذف".
    """
    user_id = query.from_user.id
    session = get_session(user_id)
    await go_home(query, context, session["selected"])


async def show_delete_menu(query, context, return_to):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()

    if not session["selected"]:
        # حالة احتياطية: لا يجب أن يظهر الزر أصلًا بدون اختيار، لكن نحتاط لها
        await go_home(query, context, session["selected"])
        return

    text = "اختر المادة التي تريد حذفها من قائمة اختياراتك:"
    keyboard = build_delete_keyboard(years_data, session["selected"], return_to)
    await query.edit_message_text(text, reply_markup=keyboard)


async def delete_course(query, context, year, code, return_to):
    user_id = query.from_user.id
    session = get_session(user_id)
    session["selected"] = [
        (y, c) for (y, c) in session["selected"] if not (y == year and c == code)
    ]
    await _return_after_delete(query, context, return_to)


async def cancel_delete(query, context, return_to):
    await _return_after_delete(query, context, return_to)


async def _return_after_delete(query, context, return_to):
    """يعيد المستخدم إلى الشاشة التي جاء منها قبل فتح قائمة الحذف."""
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()

    if return_to == "home":
        await go_home(query, context, session["selected"])
        return

    if return_to.startswith("year-"):
        year = int(return_to.split("-", 1)[1])
        selected_codes = {code for (y, code) in session["selected"] if y == year}
        has_selection = len(session["selected"]) > 0
        year_label = YEAR_NAMES.get(year, f"السنة {year}")
        text = f"مواد {year_label}\n\nاختر مادة لعرض جدولها:"
        await query.edit_message_text(
            text,
            reply_markup=build_year_courses_keyboard(years_data, year, selected_codes, has_selection),
        )
        return

    # احتياط: قيمة غير متوقعة -> الرجوع للشاشة الرئيسية
    await go_home(query, context, session["selected"])


async def show_schedule(query, context):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()

    selected_snapshot = list(session["selected"])  # نسخة قبل التصفير
    text = build_full_schedule_text(years_data, selected_snapshot)

    await query.edit_message_text(text)

    if selected_snapshot:
        os.makedirs(TEMP_DIR, exist_ok=True)
        pdf_path = os.path.join(TEMP_DIR, f"schedule_{user_id}.pdf")
        try:
            pdf_export.build_schedule_pdf(years_data, selected_snapshot, pdf_path)
            with open(pdf_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    filename="schedule.pdf",
                )
        except Exception:
            logger.exception("فشل إنشاء أو إرسال ملف PDF للجدول")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="تعذّر إنشاء ملف PDF للجدول، لكن الجدول النصي أعلاه يحتوي على كل المعلومات.",
            )
        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass

    # بعد عرض الجدول وإرساله، تُصفَّر كل الاختيارات لتبدأ جلسة جديدة من الصفر.
    reset_session(user_id)
    years_data = sd.load_courses()
    home_text, home_keyboard = home_text_and_keyboard(
        years_data, [], intro_note="تم تصفير اختياراتك، يمكنك البدء من جديد.\n\n"
    )
    await context.bot.send_message(
        chat_id=query.message.chat_id, text=home_text, reply_markup=home_keyboard
    )


async def run_update_schedule(query, context):
    remaining = cooldown_remaining_seconds()
    if remaining > 0:
        await query.answer(
            f"يرجى الانتظار {format_remaining(remaining)} قبل إعادة التحديث.", show_alert=True
        )
        return

    user_id = query.from_user.id
    session = get_session(user_id)
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
    years_data = sd.load_courses(force_reload=True)

    if success:
        note = "تم تحديث الجدول بنجاح بأحدث البيانات من موقع الجامعة.\n\n"
    elif had_data_before:
        note = "لم يكتمل التحديث بنجاح. سيتم الاستمرار باستخدام البيانات الموجودة من آخر تحديث ناجح.\n\n"
    else:
        note = (
            "فشل التحديث ولا توجد بيانات جدول متاحة حتى الآن.\n"
            "هذا يعني عادة أن السكربت لم يتمكن من الوصول إلى موقع الجامعة، "
            "أو أن بنية الموقع قد تغيّرت.\n"
        )
        if error_snippet:
            note += f"تفاصيل: {error_snippet}\n"
        note += "\n"

    text, keyboard = home_text_and_keyboard(years_data, session["selected"], intro_note=note)
    await query.edit_message_text(text, reply_markup=keyboard)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

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

    if data == "back_home":
        await query.answer()
        await back_home(query, context)
        return

    if data.startswith("delete_menu:"):
        await query.answer()
        return_to = data.split(":", 1)[1]
        await show_delete_menu(query, context, return_to)
        return

    if data.startswith("delete_course:"):
        await query.answer()
        _, year_str, code, return_to = data.split(":", 3)
        await delete_course(query, context, int(year_str), code, return_to)
        return

    if data.startswith("cancel_delete:"):
        await query.answer()
        return_to = data.split(":", 1)[1]
        await cancel_delete(query, context, return_to)
        return

    if data.startswith("year:"):
        await query.answer()
        year = int(data.split(":")[1])
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

    await query.answer()


async def healthcheck(request):
    """
    مسار بسيط يرجّع 200 OK دائمًا. هذا ما تستخدمه خدمة "التشعيل الدوري"
    الخارجية (uptime pinger) لإبقاء خدمة Render مستيقظة ومنعها من الدخول
    في وضع السكون بعد فترة من عدم النشاط.
    """
    return PlainTextResponse("OK")


async def run_webhook_server(app):
    """
    يشغّل البوت بوضع Webhook عبر خادم مخصص (starlette + uvicorn) بدل
    الخادم المدمج في المكتبة، لأن الخادم المدمج يرفض أي طلب على مسارات
    أخرى (يرد 405)، بينما نحتاج هنا مسار /healthcheck يستجيب دائمًا
    بنجاح لخدمة الـ ping الخارجية التي تمنع Render من تعطيل الخدمة.
    """
    port = int(os.environ.get("PORT", "10000"))
    external_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    webhook_path = "webhook"
    webhook_url = f"{external_url.rstrip('/')}/{webhook_path}"
    secret_token = os.environ.get("WEBHOOK_SECRET") or None

    async def telegram_webhook(request):
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

    async with app:
        await app.bot.set_webhook(url=webhook_url, secret_token=secret_token, drop_pending_updates=True)
        await app.start()
        try:
            await webserver.serve()
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

    # وضع Webhook: يُستخدم تلقائيًا عند التشغيل على استضافة سحابية مثل
    # Render، التي تضبط RENDER_EXTERNAL_URL تلقائيًا لكل خدمة. يمكن أيضًا
    # ضبط WEBHOOK_URL يدويًا على أي منصة أخرى لتفعيل هذا الوضع.
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
        app.add_handler(CallbackQueryHandler(button_handler))
        asyncio.run(run_webhook_server(app))
    else:
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(button_handler))
        logger.info("بدء تشغيل البوت (long polling)...")
        app.run_polling()


if __name__ == "__main__":
    main()
