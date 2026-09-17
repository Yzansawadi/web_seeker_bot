#!/usr/bin/env python3
# -*-غ-8 -*-
# -*- coding: utf-8 -*-
"""
bot.py
------
بوت تيليغرام لجدول مواد جامعة IUST (WEB SEEKER).

رصد التغييرات المهمة فقط (مع تصحيح منطق Set-based لمنع التبديل الوهمي للجلسات المتطابقة):
- تغيير وقت المادة أو يومها.
- إضافة مواد جديدة.
- حذف مواد من الجدول.
إرسال التقرير كملف PDF مع تنبيه قصير والعودة التلقائية للقائمة الرئيسية.
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

YEAR_NAMES = {
    1: "السنة الأولى",
    2: "السنة الثانية",
    3: "السنة الثالثة",
    4: "السنة الرابعة",
    5: "السنة الخامسة",
}

user_sessions = {}
seen_users = set()
_bot_start_time = datetime.now(timezone.utc)


def track_user(user):
    is_new = user.id not in seen_users
    seen_users.add(user.id)
    if is_new:
        name = user.full_name or user.username or str(user.id)
        logger.info("مستخدم جديد بدأ استخدام البوت: %s (المعرف: %s)", name, user.id)
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
    kind = descriptor[0] if isinstance(descriptor, tuple) else "start"

    if kind == "selection":
        years_data = sd.load_courses()
        mode = descriptor if len(descriptor) > 1 else None
        session["mode"] = mode
        text, keyboard = selection_text_and_keyboard(years_data, mode, session["selected"])
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if kind == "year":
        year_val = descriptor if len(descriptor) > 1 else 1
        await show_year_courses(query, context, year_val)
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
# رصد التغييرات المهمة (باستخدام Sets لمنع خلط الأوقات المتطابقة)
# ---------------------------------------------------------------------------

def compare_critical_changes(old_data, new_data):
    time_changes = []
    additions = []
    deletions = []
    
    old_courses = {}
    if old_data:
        for y in sd.get_years(old_data):
            for c in sd.get_courses_for_year(old_data, y):
                old_courses[(y, c['code'])] = c
                
    new_courses = {}
    if new_data:
        for y in sd.get_years(new_data):
            for c in sd.get_courses_for_year(new_data, y):
                new_courses[(y, c['code'])] = c

    # 1. إضافات مواد كاملة
    for key, new_c in new_courses.items():
        if key not in old_courses:
            additions.append(f"إضافة مادة: **{new_c['name']}** (السنة {key[0]})")

    # 2. حذف مواد كاملة
    for key, old_c in old_courses.items():
        if key not in new_courses:
            deletions.append(f"حذف مادة: **{old_c['name']}** (السنة {key[0]})")

    # 3. مقارنة الأوقات/الأيام للمواد المشتركة بمنطق Set-based دقيق
    def extract_session_set(course_obj):
        s_set = set()
        for s in course_obj.get('sessions', []) or []:
            act = str(s.get('activity') or "").strip()
            day = str(s.get('day') or "").strip()
            st = str(s.get('start') or "").strip()
            en = str(s.get('end') or "").strip()
            s_set.add((act, day, st, en))
        return s_set

    for key, new_c in new_courses.items():
        if key in old_courses:
            old_c = old_courses[key]
            course_name = new_c['name']
            
            old_set = extract_session_set(old_c)
            new_set = extract_session_set(new_c)

            if old_set != new_set:
                removed = old_set - new_set
                added = new_set - old_set
                
                details = []
                for act, day, st, en in removed:
                    details.append(f"إلغاء/تعديل جلسة قديمة: [{act} - {day} | {st}-{en}]")
                for act, day, st, en in added:
                    details.append(f"إضافة/تعديل جلسة جديدة: [{act} - {day} | {st}-{en}]")
                
                time_changes.append(f"**{course_name}**:\n  " + "\n  ".join(details))

    report_lines = []
    if additions:
        report_lines.append("🆕 المواد المضافة:")
        for item in additions: report_lines.append(f"  • {item}")
        report_lines.append("")
        
    if deletions:
        report_lines.append("❌ المواد المحذوفة:")
        for item in deletions: report_lines.append(f"  • {item}")
        report_lines.append("")
        
    if time_changes:
        report_lines.append("⏱️ التعديلات في أوقات أو أيام المحاضرات والجلسات:")
        for item in time_changes: report_lines.append(f"  • {item}")
        report_lines.append("")

    if report_lines:
        return "\n".join(report_lines)
    return None


def create_critical_html(report_text):
    safe_body = report_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
    html = f"""
    <html dir="rtl" lang="ar">
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Tahoma, sans-serif; padding: 25px; line-height: 1.8; color: #333; background: #fdfdfd; font-size: 13px; }}
            h1 {{ color: #2c3e50; text-align: center; border-bottom: 3px solid #3498db; padding-bottom: 12px; }}
            .content {{ background: #fff; padding: 15px 20px; margin-bottom: 15px; border-radius: 8px; border-right: 5px solid #3498db; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        </style>
    </head>
    <body>
        <h1>تقرير التغييرات المهمة في الجدول (WEB SEEKER)</h1>
        <div class="content">
            {safe_body}
        </div>
    </body>
    </html>
    """
    return html


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

def build_full_schedule_text(years_data, selected_list):
    if not selected_list:
        return "لم تقم باختيار أي مادة حتى الآن."
    all_sessions = []
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    track_user(update.effective_user)
    reset_session(user_id)
    text, keyboard = start_text_and_keyboard(intro_note=" اهلا بك في بوت WEB SEEKER. \n\n")
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
                    filename="times_schedule.pdf",
                )
        except Exception:
            logger.exception("فشل إنشاء أو إرسال ملف PDF للجدول")
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
    
    old_years_data = sd.load_courses() if had_data_before else None

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
    except Exception as exc:
        success = False
        error_snippet = str(exc)[-300:]

    _last_update_ts["value"] = time.time()
    
    sd.load_courses(force_reload=True)
    new_years_data = sd.load_courses()
    
    asyncio.create_task(asyncio.to_thread(notifier.notify_update_result, success, error_snippet))

    if success:
        note = "تم تحديث الجدول بنجاح بأحدث البيانات من موقع الجامعة.\n\n"
        
        if old_years_data and new_years_data:
            report_text = compare_critical_changes(old_years_data, new_years_data)
            
            if report_text:
                short_msg = (
                    "⚠️ **رصد تغييرات مهمة حقيقية (أوقات/إضافات/حذف) في الجدول!**\n"
                    "📄 التفاصيل الكاملة مدرجة في ملف الـ PDF أدناه."
                )
                await context.bot.send_message(chat_id=query.message.chat_id, text=short_msg, parse_mode='Markdown')
                
                os.makedirs(TEMP_DIR, exist_ok=True)
                changes_pdf_path = os.path.join(TEMP_DIR, f"WEB_SEEKER_critical_updates_{user_id}.pdf")
                try:
                    from weasyprint import HTML
                    html_content = create_critical_html(report_text)
                    HTML(string=html_content).write_pdf(changes_pdf_path)
                    
                    with open(changes_pdf_path, "rb") as f:
                        await context.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=f,
                            filename="WEB_SEEKER_critical_updates.pdf",
                            caption="📄 تقرير التغييرات المهمة"
                        )
                except ImportError:
                    logger.warning("مكتبة weasyprint غير مثبتة.")
                except Exception as e:
                    logger.error(f"خطأ PDF: {e}")
                finally:
                    if os.path.exists(changes_pdf_path):
                        try:
                            os.remove(changes_pdf_path)
                        except OSError:
                            pass
            else:
                await context.bot.send_message(
                    chat_id=query.message.chat_id, 
                    text="✅ تم التحديث بنجاح، ولم يتم رصد أي تبديل حقيقي في أوقات أو أيام المواد أو إضافات/حذف."
                )
    elif had_data_before:
        note = "لم يكتمل التحديث بنجاح. سيتم الاستمرار بالبيانات القديمة.\n\n"
    else:
        note = "فشل التحديث ولا توجد بيانات متاحة.\n"
        if error_snippet:
            note += f"تفاصيل: {error_snippet}\n"
        note += "\n"

    reset_session(user_id)
    
    # العودة الفورية للقائمة الرئيسية
    start_text, start_kb = start_text_and_keyboard(intro_note=note)
    try:
        await query.edit_message_text(start_text, reply_markup=start_kb)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=start_text, reply_markup=start_kb)


async def send_all_times_pdf(query, context):
    await query.answer()
    await query.edit_message_text("جاري تجميع أوقات جميع المواد في ملف PDF...")
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
        logger.exception("خطأ PDF جميع الأوقات")
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


def build_optimized_text(result):
    if result["timed_out"] and not result["schedules"]:
        return "انتهى وقت المعالجة."
    if not result["schedules"]:
        no_data = result.get("no_data_courses", [])
        if no_data:
            return "مواد بدون أوقات: " + "، ".join(no_data)
        return "لم يتم إيجاد جدول ممكن."
    best = result["schedules"][0]
    lines = ["الجدول المثالي المقترح\n"]
    lines.append(f"عدد أيام الحضور: {best['days_count']}")
    lines.append(f"مجموع الفراغات: {best['total_gap_minutes']} دقيقة")
    by_day = {}
    for o in best["options"]:
        for s in o.sessions:
            by_day.setdefault(s["day"], []).append((s["start_min"], o.course_name, o.activity, s))
    ordered_days = [d for d in sd.DAY_ORDER if d in by_day]
    for day in ordered_days:
        lines.append(f"\n{day}")
        for _, name, activity, s in sorted(by_day[day], key=lambda x: x[0]):
            lines.append(f"  {s['start']} – {s['end']}  —  {name} ({activity})")
    return "\n".join(lines)


async def optimize_schedule(query, context):
    user_id = query.from_user.id
    session = get_session(user_id)
    years_data = sd.load_courses()
    if not session["selected"]:
        await query.answer("لا توجد مواد مختارة.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text("جاري تحليل الجدول المثالي...")
    selected_snapshot = list(session["selected"])
    try:
        result = await asyncio.to_thread(
            opt.find_best_schedules,
            years_data, selected_snapshot,
            sd.get_course, sd.time_to_minutes,
            top_n=1, time_budget_seconds=8.0,
        )
    except Exception:
        logger.exception("خطأ تحسين")
        reset_session(user_id)
        start_text, start_kb = start_text_and_keyboard()
        await context.bot.send_message(chat_id=query.message.chat_id, text=start_text, reply_markup=start_kb)
        return
    result_text = build_optimized_text(result)
    await context.bot.send_message(chat_id=query.message.chat_id, text=result_text)
    if result["schedules"]:
        best = result["schedules"][0]
        os.makedirs(TEMP_DIR, exist_ok=True)
        pdf_path = os.path.join(TEMP_DIR, f"optimal_{user_id}.pdf")
        try:
            stats_lines = [f"أيام: {best['days_count']} | فراغات: {best['total_gap_minutes']}د"]
            pdf_export.build_optimized_schedule_pdf(best["options"], pdf_path, stats_lines=stats_lines)
            with open(pdf_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    filename="webseeker_schedule.pdf",
                    caption="الجدول المثالي PDF",
                )
        except Exception:
            pass
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
    data = query.data
    track_user(query.from_user)
    user_id = query.from_user.id
    session = get_session(user_id)

    if data == "update_cooldown":
        remaining = cooldown_remaining_seconds()
        await query.answer(f"التحديث متاح بعد {format_remaining(remaining)}.", show_alert=True)
        return

    if data == "update_schedule":
        await run_update_schedule(query, context)
        return

    if data.startswith("mode:"):
        await query.answer()
        chosen_mode = data.split(":", 1) if ":" in data else "show"
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
        origin = data.split(":", 1) if ":" in data else "home"
        if origin.startswith("year-"):
            try:
                y_val = int(origin.split("-", 1))
            except ValueError:
                y_val = 1
            push_screen(session, screen_year(y_val))
        else:
            push_screen(session, screen_selection(session.get("mode") or "show"))
        await show_delete_menu(query, context)
        return

    if data.startswith("delete_course:"):
        await query.answer()
        parts = data.split(":")
        if len(parts) >= 3:
            try:
                await delete_course(query, context, int(parts), parts)
            except ValueError:
                pass
        return

    if data.startswith("year:"):
        await query.answer()
        parts = data.split(":")
        if len(parts) >= 2:
            try:
                year = int(parts)
                push_screen(session, screen_selection(session.get("mode") or "show"))
                await show_year_courses(query, context, year)
            except ValueError:
                pass
        return

    if data.startswith("course:"):
        await query.answer()
        parts = data.split(":")
        if len(parts) >= 3:
            try:
                await select_course(query, context, int(parts), parts)
            except ValueError:
                pass
        return

    if data == "show_schedule":
        await query.answer()
        await show_schedule(query, context)
        return

    if data == "optimize_schedule":
        await optimize_schedule(query, context)
        return
    await query.answer()


async def healthcheck(request):
    return PlainTextResponse("OK")


async def run_webhook_server(app):
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
    async with app:
        await app.bot.set_webhook(url=webhook_url, secret_token=secret_token, drop_pending_updates=True)
        await app.start()
        try:
            await webserver.serve()
        finally:
            await app.stop()


def main():
    if BOT_TOKEN == "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("خطأ: يجب ضبط توكن البوت أولًا.")
        return
    external_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if external_url:
        app = Application.builder().token(BOT_TOKEN).updater(None).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats))
        app.add_handler(CallbackQueryHandler(button_handler))
        asyncio.run(run_webhook_server(app))
    else:
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stats", stats))
        app.add_handler(CallbackQueryHandler(button_handler))
        app.run_polling()


if __name__ == "__main__":
    main()