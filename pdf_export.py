#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_export.py
--------------
يبني ملف PDF منسّق بشكل احترافي للجدول الدراسي النهائي، مقسّم حسب الأيام
وبترتيب زمني داخل كل يوم، بدعم كامل للنص العربي (تشكيل الحروف + اتجاه
الكتابة من اليمين لليسار) عبر وحدة arabic_text المرفقة (بدون أي مكتبات
خارجية إضافية -- فقط reportlab).

أهم نقطة فنية: ReportLab لا يدعم تشكيل الحروف العربية ولا اتجاه RTL من
تلقاء نفسه، لذلك يجب تمرير كل نص عربي عبر arabic_text.prepare() قبل
رسمه، وإلا ستظهر الحروف منفصلة وبالترتيب الخاطئ.
"""

import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

import schedule_data as sd
import arabic_text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

# اسم الخط العربي المسجَّل لدى reportlab
FONT_NAME = "Arabic"
FONT_NAME_BOLD = "Arabic-Bold"

# اسم خط الشعار (الرأس الإنجليزي "WebSeeker") المسجَّل لدى reportlab
BRAND_FONT_NAME = "Orbitron-Bold"
BRAND_FONT_PATH = os.path.join(FONTS_DIR, "Orbitron-Bold.ttf")
LOGO_PATH = os.path.join(FONTS_DIR, "iust_logo.png")

_font_registered = False
_brand_font_registered = False


def _register_fonts():
    """
    يسجّل خطًا يدعم العربية لدى ReportLab. يبحث عن ملفات خطوط TTF داخل
    مجلد fonts بجانب هذا الملف (باستثناء خط الشعار Orbitron الذي له
    دالة تسجيل مستقلة تمامًا، حتى لا يختلط الاثنان). إن لم يجد أي خط
    عربي، يرفع خطأ واضحًا يوجّه المستخدم لوضع ملف خط مناسب هناك (Amiri أو
    NotoNaskhArabic مثلاً) بدل أن يفشل بصمت أو برسالة غامضة من ReportLab.
    """
    global _font_registered
    if _font_registered:
        return

    regular_path = None
    bold_path = None

    if os.path.isdir(FONTS_DIR):
        for fname in os.listdir(FONTS_DIR):
            lower = fname.lower()
            if not lower.endswith(".ttf"):
                continue
            if "orbitron" in lower:
                continue  # خط الشعار له تسجيل مستقل، ليس جزءًا من نظام الخط العربي
            full = os.path.join(FONTS_DIR, fname)
            if "bold" in lower and bold_path is None:
                bold_path = full
            elif regular_path is None:
                regular_path = full

    if regular_path is None:
        raise RuntimeError(
            "لم يتم العثور على خط عربي. ضع ملف خط TTF يدعم العربية "
            f"(مثل Amiri-Regular.ttf) داخل المجلد: {FONTS_DIR}\n"
            "يمكن تحميل خط Amiri المجاني من Google Fonts."
        )

    if bold_path is None:
        bold_path = regular_path  # استخدام نفس الخط إن لم يوجد خط عريض مخصص

    pdfmetrics.registerFont(TTFont(FONT_NAME, regular_path))
    pdfmetrics.registerFont(TTFont(FONT_NAME_BOLD, bold_path))
    _font_registered = True


def _register_brand_font():
    """
    يسجّل خط Orbitron (المستخدم فقط لاسم WebSeeker في رأس الصفحة) بمسار
    صريح ومستقل تمامًا عن منطق اختيار الخط العربي. إن لم يوجد الملف، لا
    يرفع خطأ قاتلاً -- فقط يُعطّل رسم اسم WebSeeker (يبقى الشعار والجدول
    يعملان بشكل طبيعي تمامًا) ليبقى ملف PDF قابلاً للإنشاء حتى لو نُسي
    رفع هذا الملف بالخطأ.
    """
    global _brand_font_registered
    if _brand_font_registered:
        return True

    if not os.path.isfile(BRAND_FONT_PATH):
        return False

    pdfmetrics.registerFont(TTFont(BRAND_FONT_NAME, BRAND_FONT_PATH))
    _brand_font_registered = True
    return True


def _ar(text):
    """يجهّز نصًا عربيًا للرسم الصحيح: تشكيل الحروف + اتجاه RTL."""
    if text is None:
        return ""
    return arabic_text.prepare(str(text))


# ---------------------------------------------------------------------------
# تخطيط الصفحة
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = A4
MARGIN = 15 * mm
RIGHT_X = PAGE_W - MARGIN          # نبدأ الكتابة من أقصى اليمين (RTL)
LEFT_X = MARGIN
CONTENT_W = PAGE_W - 2 * MARGIN

TITLE_SIZE = 18
DAY_HEADER_SIZE = 14
COURSE_NAME_SIZE = 12
DETAIL_SIZE = 10
LINE_GAP = 6 * mm

# ---- تصميم رأس الصفحة (الشريط الأزرق + الشعار + اسم WebSeeker) ----
# الأزرق مأخوذ فعليًا من الدائرة الداخلية لشعار الجامعة لضمان التناسق
# البصري الكامل بين الشريط والشعار، لا لون عشوائي منفصل عنه.
HEADER_HEIGHT = 22 * mm
HEADER_BG_COLOR = "#005078"
HEADER_ACCENT_COLOR = "#E6BE00"  # خط فاصل رفيع بلون أصفر الشعار
HEADER_LOGO_SIZE = 15 * mm
BRAND_NAME_SIZE = 19
BRAND_TEXT = "WebSeeker"


def _new_page(c):
    c.showPage()
    _draw_header(c)
    return PAGE_H - HEADER_HEIGHT - 8 * mm


def _draw_header(c):
    """
    يرسم شريط الرأس العلوي: خلفية زرقاء بعرض الصفحة الكامل، شعار الجامعة
    على الجهة اليسرى، واسم "WebSeeker" بخط Orbitron الأبيض إلى يمين
    الشعار مباشرة، وخط فاصل أصفر رفيع أسفل الشريط. يُستدعى مرة في بداية
    كل صفحة (الأولى والصفحات اللاحقة عند الحاجة)، لتظهر الهوية البصرية
    باستمرار حتى لو امتد الجدول لأكثر من صفحة.
    """
    c.setFillColor(colors.HexColor(HEADER_BG_COLOR))
    c.rect(0, PAGE_H - HEADER_HEIGHT, PAGE_W, HEADER_HEIGHT, fill=1, stroke=0)

    logo_x = MARGIN
    logo_y = PAGE_H - HEADER_HEIGHT + (HEADER_HEIGHT - HEADER_LOGO_SIZE) / 2
    if os.path.isfile(LOGO_PATH):
        logo = ImageReader(LOGO_PATH)
        c.drawImage(
            logo, logo_x, logo_y,
            width=HEADER_LOGO_SIZE, height=HEADER_LOGO_SIZE,
            mask="auto", preserveAspectRatio=True,
        )

    if _register_brand_font():
        c.setFillColor(colors.white)
        c.setFont(BRAND_FONT_NAME, BRAND_NAME_SIZE)
        text_x = logo_x + HEADER_LOGO_SIZE + 4 * mm
        text_baseline_y = PAGE_H - HEADER_HEIGHT / 2 - (BRAND_NAME_SIZE * 0.32)
        c.drawString(text_x, text_baseline_y, BRAND_TEXT)

    c.setStrokeColor(colors.HexColor(HEADER_ACCENT_COLOR))
    c.setLineWidth(1.5)
    c.line(0, PAGE_H - HEADER_HEIGHT, PAGE_W, PAGE_H - HEADER_HEIGHT)

    c.setFillColor(colors.black)  # إعادة الحالة الافتراضية لما بعد رسم الرأس


def _wrap_text_to_width(c, text, font_name, font_size, max_width):
    """
    يلف نصًا عربيًا مُجهَّزًا (بعد _ar) إلى عدة أسطر بحيث لا يتجاوز كل سطر
    max_width فعليًا (بالنقاط)، بالاعتماد على stringWidth الحقيقي للخط
    المُستخدَم بدل افتراض عدد أحرف ثابت. ملاحظة: النص يجب أن يكون قد مرّ
    بالفعل عبر arabic_text.prepare (الاتجاه البصري معكوس)، لذا نقسّمه إلى
    "كلمات" بحسب المسافات كما هي، ونبني الأسطر بنفس الترتيب المُعطى.
    """
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_sessions_body(c, y, all_sessions, extra_intro_lines=None):
    """
    يرسم جسم الجدول (الأيام + الجلسات) بدءًا من الإحداثي y المُعطى.
    all_sessions: قائمة (day, start_min, course_name, session_dict).
    extra_intro_lines: أسطر نصية اختيارية تُرسَم قبل الأيام مباشرة (تُستخدم
    لصندوق إحصائيات الجودة في ملف الجدول المثالي)، كل عنصر (نص، حجم خط).
    يُلَفّ كل نص طويل تلقائيًا على عدة أسطر فعلية بحسب عرضه الحقيقي، حتى
    لا يتداخل بصريًا مع ما يليه (كان هذا خللاً قبل هذا الإصلاح).

    تُعاد القيمة النهائية لـ y بعد كل الرسم (غير مستخدمة حاليًا لكنها
    مفيدة لو احتاج مستدعٍ مستقبلي إضافة محتوى بعدها).
    """
    if extra_intro_lines:
        max_text_width = CONTENT_W - 6 * mm
        for text, size in extra_intro_lines:
            c.setFont(FONT_NAME, size)
            c.setFillColor(colors.HexColor("#1F3864"))
            wrapped = _wrap_text_to_width(c, _ar(text), FONT_NAME, size, max_text_width)
            line_height = size * 1.5  # بالنقاط (1pt = 1/72 إنش)؛ نطبّق فرقًا كافيًا بين الأسطر
            for wrapped_line in wrapped:
                c.drawCentredString(PAGE_W / 2, y, wrapped_line)
                y -= line_height * 0.3528 * mm / mm  # 1pt -> mm تقريبًا (0.3528mm/pt)، نُبقي القيمة بوحدة reportlab (نقاط أصلاً)
        c.setFillColor(colors.black)
        y -= 4 * mm

    by_day = {}
    for day, start_min, name, s in all_sessions:
        by_day.setdefault(day, []).append((start_min, name, s))

    ordered_days = [d for d in sd.DAY_ORDER if d in by_day]

    if not ordered_days:
        c.setFont(FONT_NAME, DETAIL_SIZE + 2)
        c.drawCentredString(PAGE_W / 2, y, _ar("لا توجد معلومات جدول للمواد المختارة."))
        return y

    day_colors = ["#1F3864", "#2E5395", "#3D6BB3", "#4F81BD", "#6FA8DC", "#9FC5E8", "#C9DAF8"]

    for day_idx, day in enumerate(ordered_days):
        sessions_today = sorted(by_day[day], key=lambda x: x[0])

        needed_height = 12 * mm + len(sessions_today) * 16 * mm
        if y - needed_height < MARGIN:
            y = _new_page(c)

        header_color = colors.HexColor(day_colors[day_idx % len(day_colors)])
        c.setFillColor(header_color)
        c.roundRect(LEFT_X, y - 9 * mm, CONTENT_W, 9 * mm, 2 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(FONT_NAME_BOLD, DAY_HEADER_SIZE)
        c.drawCentredString(PAGE_W / 2, y - 6.5 * mm, _ar(day))
        y -= 13 * mm

        c.setFillColor(colors.black)

        for _, name, s in sessions_today:
            if y - 16 * mm < MARGIN:
                y = _new_page(c)

            activity = s["activity"]
            time_range = f"{s['start']} - {s['end']}"
            room = s["room"]
            teacher = s["teacher"]

            c.setFillColor(colors.HexColor("#F2F6FC"))
            c.roundRect(LEFT_X, y - 14 * mm, CONTENT_W, 13 * mm, 1.5 * mm, fill=1, stroke=0)
            c.setFillColor(colors.black)

            c.setFont(FONT_NAME_BOLD, COURSE_NAME_SIZE)
            c.drawRightString(RIGHT_X - 3 * mm, y - 5.5 * mm, _ar(name))

            c.setFont(FONT_NAME, DETAIL_SIZE)
            c.drawString(LEFT_X + 3 * mm, y - 5.5 * mm, time_range)

            detail_parts = [activity]
            if room:
                detail_parts.append(f"القاعة: {room}")
            if teacher:
                detail_parts.append(teacher)
            detail_text = "  |  ".join(detail_parts)

            c.setFont(FONT_NAME, DETAIL_SIZE - 1)
            c.setFillColor(colors.HexColor("#444444"))
            c.drawRightString(RIGHT_X - 3 * mm, y - 11 * mm, _ar(detail_text))
            c.setFillColor(colors.black)

            y -= 16 * mm

        y -= 4 * mm

    return y


def _draw_footer(c):
    c.setFont(FONT_NAME, 8)
    c.setFillColor(colors.HexColor("#888888"))
    c.drawCentredString(PAGE_W / 2, MARGIN / 2, _ar("تم إنشاء هذا الجدول تلقائيًا بواسطة بوت جدول IUST"))


def build_schedule_pdf(years_data, selected_list, output_path, student_name=None):
    """
    يبني ملف PDF للجدول النهائي.

    years_data: المخرجات من schedule_data.load_courses()
    selected_list: قائمة (year, code) بترتيب الاختيار
    output_path: المسار الذي سيُحفظ فيه الملف
    student_name: اسم اختياري يظهر في رأس الصفحة (غير مستخدم حاليًا من البوت)
    """
    _register_fonts()

    c = canvas.Canvas(output_path, pagesize=A4)
    _draw_header(c)
    y = PAGE_H - HEADER_HEIGHT - 8 * mm

    c.setFont(FONT_NAME_BOLD, TITLE_SIZE)
    c.drawCentredString(PAGE_W / 2, y, _ar("الجدول الدراسي الأسبوعي"))
    y -= 10 * mm

    c.setStrokeColor(colors.HexColor("#1F3864"))
    c.setLineWidth(1)
    c.line(LEFT_X, y, RIGHT_X, y)
    y -= 8 * mm

    all_sessions = []
    for year, code in selected_list:
        course = sd.get_course(years_data, year, code)
        if not course:
            continue
        for s in course["sessions"]:
            all_sessions.append((s["day"], s["start_min"], course["name"], s))

    _draw_sessions_body(c, y, all_sessions)
    _draw_footer(c)
    c.save()


def build_optimized_schedule_pdf(chosen_options, output_path, stats_lines=None):
    """
    يبني ملف PDF لنتيجة "توليد جدول مثالي" من محرك schedule_optimizer.

    chosen_options: قائمة كائنات SectionOption (شُعبة واحدة محدَّدة بالضبط
    لكل مادة/نشاط، لا كل شُعب المادة كما في build_schedule_pdf العادية).
    stats_lines: أسطر نصية اختيارية (مثل "عدد أيام الحضور: 3") تُعرض في
    صندوق إحصائيات أعلى الجدول لإبراز جودة الحل المختار.
    """
    _register_fonts()

    c = canvas.Canvas(output_path, pagesize=A4)
    _draw_header(c)
    y = PAGE_H - HEADER_HEIGHT - 8 * mm

    c.setFont(FONT_NAME_BOLD, TITLE_SIZE)
    c.drawCentredString(PAGE_W / 2, y, _ar("الجدول المثالي المُقترَح"))
    y -= 10 * mm

    c.setStrokeColor(colors.HexColor("#1F3864"))
    c.setLineWidth(1)
    c.line(LEFT_X, y, RIGHT_X, y)
    y -= 8 * mm

    all_sessions = []
    for opt in chosen_options:
        for s in opt.sessions:
            all_sessions.append((s["day"], s["start_min"], opt.course_name, s))

    intro = [(line, DETAIL_SIZE + 1) for line in stats_lines] if stats_lines else None
    y = _draw_sessions_body(c, y, all_sessions, extra_intro_lines=intro)
    _draw_footer(c)
    c.save()


def build_all_times_pdf(years_data, output_path):
    """
    يبني ملف PDF يحتوي على جميع المواد من كل السنوات مع كل أوقاتها وشُعبها.
    مفيد للطالب كمرجع كامل لجميع المواد المتاحة قبل اتخاذ قرار الاختيار.
    يُرتَّب المحتوى: سنة أولى ← ثانية ← ... ثالثة، وداخل كل سنة بالترتيب
    الأبجدي لأسماء المواد، وتحت كل مادة كل جلساتها مرتّبة بالأيام والأوقات.
    """
    _register_fonts()

    c = canvas.Canvas(output_path, pagesize=A4)
    _draw_header(c)
    y = PAGE_H - HEADER_HEIGHT - 8 * mm

    c.setFont(FONT_NAME_BOLD, TITLE_SIZE)
    c.drawCentredString(PAGE_W / 2, y, _ar("أوقات جميع المواد الدراسية"))
    y -= 10 * mm

    c.setStrokeColor(colors.HexColor("#1F3864"))
    c.setLineWidth(1)
    c.line(LEFT_X, y, RIGHT_X, y)
    y -= 6 * mm

    year_colors = ["#1F3864", "#2E5395", "#3D6BB3", "#4F81BD", "#6FA8DC"]

    for year in sd.get_years(years_data):
        courses = sd.get_courses_for_year(years_data, year)
        courses_with_sessions = [c2 for c2 in courses if c2["sessions"]]
        if not courses_with_sessions:
            continue

        year_label = f"السنة {['الأولى','الثانية','الثالثة','الرابعة','الخامسة'][min(year-1,4)]}"

        # رأس السنة
        if y - 14 * mm < MARGIN:
            y = _new_page(c)

        c.setFillColor(colors.HexColor(year_colors[(year - 1) % len(year_colors)]))
        c.roundRect(LEFT_X, y - 11 * mm, CONTENT_W, 11 * mm, 2 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(FONT_NAME_BOLD, TITLE_SIZE - 2)
        c.drawCentredString(PAGE_W / 2, y - 8 * mm, _ar(year_label))
        c.setFillColor(colors.black)
        y -= 15 * mm

        for course in sorted(courses_with_sessions, key=lambda x: x["name"]):
            # عنوان المادة
            if y - 10 * mm < MARGIN:
                y = _new_page(c)

            c.setFillColor(colors.HexColor("#E8F0FE"))
            c.roundRect(LEFT_X, y - 8 * mm, CONTENT_W, 8 * mm, 1 * mm, fill=1, stroke=0)
            c.setFillColor(colors.HexColor("#1F3864"))
            c.setFont(FONT_NAME_BOLD, COURSE_NAME_SIZE)
            c.drawRightString(RIGHT_X - 3 * mm, y - 6 * mm, _ar(course["name"]))
            c.setFont(FONT_NAME, DETAIL_SIZE - 1)
            c.setFillColor(colors.HexColor("#444444"))
            c.drawString(LEFT_X + 3 * mm, y - 6 * mm, course["code"])
            c.setFillColor(colors.black)
            y -= 10 * mm

            # تجميع الجلسات حسب النشاط والشعبة
            by_act = {}
            for s in course["sessions"]:
                key = (s["activity"], s["section"])
                by_act.setdefault(key, []).append(s)

            for (activity, section), sessions in sorted(by_act.items()):
                if y - 8 * mm < MARGIN:
                    y = _new_page(c)

                # تشكيل اسم اليوم فقط بشكل منفصل لمنع الخلل في ترتيب الأرقام والـ AM/PM
                formatted_sessions = []
                for s in sessions:
                    day_ar = _ar(s["day"])
                    time_range = f"{s['start']} - {s['end']}"
                    formatted_sessions.append(f"{time_range}  {day_ar}")

                days_str = "  |  ".join(formatted_sessions)

                room = sessions[0].get("room", "")
                teacher = sessions[0].get("teacher", "")

                detail = f"{activity} - شعبة {section}"
                if room:
                    detail += f" | {room}"
                if teacher:
                    detail += f" | {teacher}"

                c.setFont(FONT_NAME, DETAIL_SIZE - 1)
                c.setFillColor(colors.HexColor("#333333"))
                c.drawRightString(RIGHT_X - 5 * mm, y - 4 * mm, _ar(detail))
                c.setFillColor(colors.HexColor("#555555"))
                c.setFont(FONT_NAME, DETAIL_SIZE - 2)
                c.drawString(LEFT_X + 5 * mm, y - 4 * mm, days_str)
                c.setFillColor(colors.black)
                y -= 7 * mm

            y -= 3 * mm

        y -= 5 * mm

    _draw_footer(c)
    c.save()