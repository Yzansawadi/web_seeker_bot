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

import schedule_data as sd
import arabic_text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

# اسم الخط المسجَّل لدى reportlab (يجب أن يدعم الحروف العربية)
FONT_NAME = "Arabic"
FONT_NAME_BOLD = "Arabic-Bold"

_font_registered = False


def _register_fonts():
    """
    يسجّل خطًا يدعم العربية لدى ReportLab. يبحث عن ملفات خطوط TTF داخل
    مجلد fonts بجانب هذا الملف. إن لم يجد أي خط، يرفع خطأ واضحًا يوجّه
    المستخدم لوضع ملف خط مناسب هناك (Amiri أو NotoNaskhArabic مثلاً)
    بدل أن يفشل بصمت أو برسالة غامضة من ReportLab.
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


def _new_page(c):
    c.showPage()
    return PAGE_H - MARGIN


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
    y = PAGE_H - MARGIN

    # ---- العنوان الرئيسي ----
    c.setFont(FONT_NAME_BOLD, TITLE_SIZE)
    c.drawCentredString(PAGE_W / 2, y, _ar("الجدول الدراسي الأسبوعي"))
    y -= 10 * mm

    c.setStrokeColor(colors.HexColor("#1F3864"))
    c.setLineWidth(1)
    c.line(LEFT_X, y, RIGHT_X, y)
    y -= 8 * mm

    # ---- تجميع الجلسات بحسب اليوم ----
    all_sessions = []
    for year, code in selected_list:
        course = sd.get_course(years_data, year, code)
        if not course:
            continue
        for s in course["sessions"]:
            all_sessions.append((s["day"], s["start_min"], course["name"], s))

    by_day = {}
    for day, start_min, name, s in all_sessions:
        by_day.setdefault(day, []).append((start_min, name, s))

    ordered_days = [d for d in sd.DAY_ORDER if d in by_day]

    if not ordered_days:
        c.setFont(FONT_NAME, DETAIL_SIZE + 2)
        c.drawCentredString(PAGE_W / 2, y, _ar("لا توجد معلومات جدول للمواد المختارة."))
        c.save()
        return

    day_colors = ["#1F3864", "#2E5395", "#3D6BB3", "#4F81BD", "#6FA8DC", "#9FC5E8", "#C9DAF8"]

    for day_idx, day in enumerate(ordered_days):
        sessions_today = sorted(by_day[day], key=lambda x: x[0])

        # تقدير الارتفاع اللازم لرأس اليوم + كل المواد فيه، وفتح صفحة جديدة عند الحاجة
        needed_height = 12 * mm + len(sessions_today) * 16 * mm
        if y - needed_height < MARGIN:
            y = _new_page(c)

        # ---- رأس اليوم ----
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

            # خلفية خفيفة لكل مادة لتسهيل القراءة
            c.setFillColor(colors.HexColor("#F2F6FC"))
            c.roundRect(LEFT_X, y - 14 * mm, CONTENT_W, 13 * mm, 1.5 * mm, fill=1, stroke=0)
            c.setFillColor(colors.black)

            # السطر الأول: اسم المادة (يمين) + الوقت (يسار)
            c.setFont(FONT_NAME_BOLD, COURSE_NAME_SIZE)
            c.drawRightString(RIGHT_X - 3 * mm, y - 5.5 * mm, _ar(name))

            c.setFont(FONT_NAME, DETAIL_SIZE)
            c.drawString(LEFT_X + 3 * mm, y - 5.5 * mm, time_range)

            # السطر الثاني: النوع + القاعة + المدرّس
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

        y -= 4 * mm  # مسافة بين الأيام

    # ---- تذييل الصفحة ----
    c.setFont(FONT_NAME, 8)
    c.setFillColor(colors.HexColor("#888888"))
    c.drawCentredString(PAGE_W / 2, MARGIN / 2, _ar("تم إنشاء هذا الجدول تلقائيًا بواسطة بوت جدول IUST"))

    c.save()
