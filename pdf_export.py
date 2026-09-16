#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
FONT_NAME = "Arabic"
FONT_NAME_BOLD = "Arabic-Bold"
BRAND_FONT_NAME = "Orbitron-Bold"
BRAND_FONT_PATH = os.path.join(FONTS_DIR, "Orbitron-Bold.ttf")
LOGO_PATH = os.path.join(FONTS_DIR, "iust_logo.png")

_font_registered = False
_brand_font_registered = False


def _register_fonts():
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
                continue
            full = os.path.join(FONTS_DIR, fname)
            if "bold" in lower and bold_path is None:
                bold_path = full
            elif regular_path is None:
                regular_path = full
    if regular_path is None:
        raise RuntimeError(f"لم يتم العثور على خط عربي داخل: {FONTS_DIR}")
    if bold_path is None:
        bold_path = regular_path
    pdfmetrics.registerFont(TTFont(FONT_NAME, regular_path))
    pdfmetrics.registerFont(TTFont(FONT_NAME_BOLD, bold_path))
    _font_registered = True


def _register_brand_font():
    global _brand_font_registered
    if _brand_font_registered:
        return True
    if not os.path.isfile(BRAND_FONT_PATH):
        return False
    pdfmetrics.registerFont(TTFont(BRAND_FONT_NAME, BRAND_FONT_PATH))
    _brand_font_registered = True
    return True


def _ar(text):
    if text is None:
        return ""
    return arabic_text.prepare(str(text))


PAGE_W, PAGE_H = A4
MARGIN = 15 * mm
RIGHT_X = PAGE_W - MARGIN
LEFT_X = MARGIN
CONTENT_W = PAGE_W - 2 * MARGIN
TITLE_SIZE = 18
DAY_HEADER_SIZE = 14
COURSE_NAME_SIZE = 12
DETAIL_SIZE = 10
HEADER_HEIGHT = 22 * mm
HEADER_BG_COLOR = "#005078"
HEADER_ACCENT_COLOR = "#E6BE00"
HEADER_LOGO_SIZE = 15 * mm
BRAND_NAME_SIZE = 19
BRAND_TEXT = "WebSeeker"


def _new_page(c):
    c.showPage()
    _draw_header(c)
    return PAGE_H - HEADER_HEIGHT - 8 * mm


def _draw_header(c):
    c.setFillColor(colors.HexColor(HEADER_BG_COLOR))
    c.rect(0, PAGE_H - HEADER_HEIGHT, PAGE_W, HEADER_HEIGHT, fill=1, stroke=0)
    logo_x = MARGIN
    logo_y = PAGE_H - HEADER_HEIGHT + (HEADER_HEIGHT - HEADER_LOGO_SIZE) / 2
    if os.path.isfile(LOGO_PATH):
        logo = ImageReader(LOGO_PATH)
        c.drawImage(logo, logo_x, logo_y, width=HEADER_LOGO_SIZE, height=HEADER_LOGO_SIZE,
                     mask="auto", preserveAspectRatio=True)
    if _register_brand_font():
        c.setFillColor(colors.white)
        c.setFont(BRAND_FONT_NAME, BRAND_NAME_SIZE)
        text_x = logo_x + HEADER_LOGO_SIZE + 4 * mm
        text_baseline_y = PAGE_H - HEADER_HEIGHT / 2 - (BRAND_NAME_SIZE * 0.32)
        c.drawString(text_x, text_baseline_y, BRAND_TEXT)
    c.setStrokeColor(colors.HexColor(HEADER_ACCENT_COLOR))
    c.setLineWidth(1.5)
    c.line(0, PAGE_H - HEADER_HEIGHT, PAGE_W, PAGE_H - HEADER_HEIGHT)
    c.setFillColor(colors.black)


def _wrap_text_to_width(c, text, font_name, font_size, max_width):
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
    if extra_intro_lines:
        max_text_width = CONTENT_W - 6 * mm
        for text, size in extra_intro_lines:
            c.setFont(FONT_NAME, size)
            c.setFillColor(colors.HexColor("#1F3864"))
            wrapped = _wrap_text_to_width(c, _ar(text), FONT_NAME, size, max_text_width)
            line_height = size * 1.5
            for wrapped_line in wrapped:
                c.drawCentredString(PAGE_W / 2, y, wrapped_line)
                y -= line_height * 0.3528 * mm / mm
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


###############################################################################
# ملف "أوقات جميع المواد" -- تنسيق مستقل تمامًا عن بقية الملفات (لا يستخدم
# الرأس المُعلَّم WebSeeker إطلاقًا). يعرض كل مادة جُلبت من موقع الجامعة
# بشكل مسطّح دون تقسيم حسب السنة، مرتّبة أبجديًا، مع فهرس مرقّم في أول
# صفحة يشير لرقم الصفحة الفعلي لكل مادة داخل الملف.
#
# لضمان أن أرقام الصفحات في الفهرس تطابق فعليًا مكان كل مادة، يُحسَب
# التخطيط (Pagination) مرتين بنفس الثوابت والدوال تمامًا: مرة "حسابية
# بحتة" بدون أي رسم فعلي (لمعرفة أرقام الصفحات مسبقًا قبل رسم الفهرس)،
# ومرة "فعلية" بالرسم على القماشة الحقيقية. بما أن الحسابين يستخدمان نفس
# الدوال والثوابت بالضبط، تكون النتيجتان متطابقتين دائمًا.
###############################################################################

ALLTIMES_TITLE = "أوقات جميع المواد الدراسية"
ACTIVITY_ORDER = ["نظري", "عملي"]
ACTIVITY_SECTION_LABELS = {"نظري": "القسم النظري", "عملي": "القسم العملي"}

ALLTIMES_TOP_MARGIN = 10 * mm
ALLTIMES_CONTENT_TOP = PAGE_H - 20 * mm       # بداية المحتوى في أي صفحة (تحت الخط العلوي الرفيع)
ALLTIMES_INDEX_TITLE_BLOCK = 26 * mm          # المساحة التي يشغلها عنوان الفهرس في صفحته الأولى فقط
ALLTIMES_BOTTOM_LIMIT = MARGIN + 12 * mm      # حد أدنى للمساحة المتبقية أسفل الصفحة (محجوزة لرقم الصفحة)

# الفهرس على شكل جدول بعمودين (أعمدة عريضة بالإطار العربي: العمود
# الأيمن يُملأ أولًا من الأعلى للأسفل، ثم العمود الأيسر)، بصفوف بارتفاع
# ثابت (بلا التفاف نص) لحشر أكبر عدد ممكن من المواد في أقل عدد صفحات.
ALLTIMES_INDEX_COLS = 2
ALLTIMES_INDEX_ROW_H = 7 * mm
ALLTIMES_INDEX_HEADER_H = 8 * mm          # صف عناوين الأعمدة (الرقم/المادة/الصفحة)، يتكرر في كل صفحة فهرس
ALLTIMES_INDEX_GUTTER = 8 * mm            # الفراغ بين العمودين
ALLTIMES_INDEX_NUM_COL_W = 8 * mm         # عرض عمود الرقم التسلسلي (أقصى اليمين)
ALLTIMES_INDEX_PAGE_COL_W = 11 * mm       # عرض عمود رقم الصفحة (أقصى اليسار)
ALLTIMES_INDEX_FONT_SIZE = 9.5

ALLTIMES_COURSE_HEADER_H = 11 * mm
ALLTIMES_ACTIVITY_HEADER_H = 7 * mm
ALLTIMES_SESSION_LINE_H = 7 * mm
ALLTIMES_COURSE_GAP_H = 5 * mm


def _gather_all_courses_flat(years_data):
    """كل المواد التي تحتوي أوقاتًا فعلية من كل السنوات (بما فيها المواد
    غير المصنَّفة ضمن subjects.xlsx إن وُجدت)، مسطَّحة بلا تجميع حسب السنة
    ومرتَّبة أبجديًا بالاسم."""
    courses = []
    for year_courses in years_data.values():
        for course in year_courses.values():
            if course["sessions"]:
                courses.append(course)
    courses.sort(key=lambda c: c["name"])
    return courses


def _course_activity_groups(course):
    """يجمع جلسات المادة حسب نوع النشاط (نظري أولًا ثم عملي)، وداخل كل
    نشاط حسب الشُعبة، مرتَّبة."""
    by_act = {}
    for s in course["sessions"]:
        by_act.setdefault(s["activity"], {}).setdefault(s["section"], []).append(s)

    def act_key(a):
        return ACTIVITY_ORDER.index(a) if a in ACTIVITY_ORDER else len(ACTIVITY_ORDER)

    groups = []
    for act in sorted(by_act.keys(), key=act_key):
        section_items = sorted(by_act[act].items(), key=lambda kv: kv[0])
        groups.append((act, section_items))
    return groups


def _index_col_width():
    return (CONTENT_W - ALLTIMES_INDEX_GUTTER) / ALLTIMES_INDEX_COLS


def _index_name_col_width():
    return _index_col_width() - ALLTIMES_INDEX_NUM_COL_W - ALLTIMES_INDEX_PAGE_COL_W - 4 * mm


def _truncate_to_width(text, font_name, font_size, max_width):
    """يقصّ نصًا (مُجهَّزًا عربيًا مسبقًا عبر _ar) ليتناسب مع عرض ثابت مع
    إضافة "…"، بدل الالتفاف لعدة أسطر -- ضروري هنا لأن صفوف الجدول ارتفاعها
    ثابت (لا تتّسع لأكثر من سطر واحد)."""
    if pdfmetrics.stringWidth(text, font_name, font_size) <= max_width:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid] + ellipsis
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis if lo > 0 else ellipsis


def _index_rows_per_column(first_page):
    """عدد صفوف عمود واحد من الفهرس ضمن صفحة واحدة (تُحسَب صفحة الفهرس
    الأولى بشكل منفصل لأنها تحجز مساحة إضافية لعنوان الفهرس)."""
    top = ALLTIMES_CONTENT_TOP - (ALLTIMES_INDEX_TITLE_BLOCK if first_page else 0)
    usable = top - ALLTIMES_INDEX_HEADER_H - ALLTIMES_BOTTOM_LIMIT
    return max(1, int(usable // ALLTIMES_INDEX_ROW_H))


def _index_page_capacity(first_page):
    return _index_rows_per_column(first_page) * ALLTIMES_INDEX_COLS


def _compute_index_pagination(courses):
    """حساب هندسي بحت (بلا أي رسم) لعدد صفحات الفهرس. بما أن صفوف الجدول
    ثابتة الارتفاع، هذا مجرّد قسمة حسابية بسيطة (بلا حاجة لقياس نص)."""
    total = len(courses)
    first_cap = _index_page_capacity(first_page=True)
    if total <= first_cap:
        return 1
    other_cap = _index_page_capacity(first_page=False)
    remaining = total - first_cap
    return 1 + -(-remaining // other_cap)  # قسمة مع تقريب للأعلى (ceiling) بلا استيراد math


def _compute_body_pagination(courses):
    """حساب هندسي بحت (بلا أي رسم) لعدد صفحات الجسم، ولرقم الصفحة (ضمن
    الجسم فقط، بدون احتساب صفحات الفهرس) التي تبدأ عندها كل مادة."""
    page = 1
    y = ALLTIMES_CONTENT_TOP
    start_pages = {}
    for course in courses:
        groups = _course_activity_groups(course)
        first_chunk = ALLTIMES_COURSE_HEADER_H + ALLTIMES_ACTIVITY_HEADER_H + ALLTIMES_SESSION_LINE_H
        if y - first_chunk < ALLTIMES_BOTTOM_LIMIT:
            page += 1
            y = ALLTIMES_CONTENT_TOP
        start_pages[course["code"]] = page
        y -= ALLTIMES_COURSE_HEADER_H
        for act_name, sections in groups:
            if y - ALLTIMES_ACTIVITY_HEADER_H < ALLTIMES_BOTTOM_LIMIT:
                page += 1
                y = ALLTIMES_CONTENT_TOP
            y -= ALLTIMES_ACTIVITY_HEADER_H
            for _section_id, _sess_list in sections:
                if y - ALLTIMES_SESSION_LINE_H < ALLTIMES_BOTTOM_LIMIT:
                    page += 1
                    y = ALLTIMES_CONTENT_TOP
                y -= ALLTIMES_SESSION_LINE_H
        y -= ALLTIMES_COURSE_GAP_H
    return start_pages, page


def _draw_alltimes_scaffold(c, page_num, total_pages):
    """خط علوي رفيع بعنوان الملف (بدون أي شعار أو اسم WebSeeker)، ورقم
    الصفحة أسفل كل صفحة. تُستدعى في بداية كل صفحة جديدة."""
    rule_y = PAGE_H - ALLTIMES_TOP_MARGIN
    c.setStrokeColor(colors.HexColor("#1F3864"))
    c.setLineWidth(1.2)
    c.line(LEFT_X, rule_y, RIGHT_X, rule_y)
    c.setFont(FONT_NAME_BOLD, 10)
    c.setFillColor(colors.HexColor("#1F3864"))
    c.drawRightString(RIGHT_X, rule_y + 3 * mm, _ar(ALLTIMES_TITLE))
    c.setFillColor(colors.black)

    c.setFont(FONT_NAME, 9)
    c.setFillColor(colors.HexColor("#888888"))
    c.drawCentredString(PAGE_W / 2, MARGIN / 2, f"{page_num} / {total_pages}")
    c.setFillColor(colors.black)


def _draw_index_title_block(c, total_courses):
    y = PAGE_H - ALLTIMES_TOP_MARGIN - 10 * mm
    c.setFont(FONT_NAME_BOLD, 20)
    c.setFillColor(colors.HexColor("#1F3864"))
    c.drawCentredString(PAGE_W / 2, y, _ar("فهرس أوقات جميع المواد الدراسية"))
    y -= 9 * mm
    c.setFont(FONT_NAME, 12)
    c.setFillColor(colors.HexColor("#444444"))
    c.drawCentredString(PAGE_W / 2, y, _ar(f"عدد المواد: {total_courses}"))
    y -= 6 * mm
    c.setStrokeColor(colors.HexColor("#1F3864"))
    c.setLineWidth(1)
    c.line(LEFT_X, y, RIGHT_X, y)
    c.setFillColor(colors.black)


def _index_column_x(col):
    """(حافة العمود اليمنى، حافة العمود اليسرى) -- العمود 0 هو الأيمن
    (يُقرأ أولًا في السياق العربي)، والعمود 1 يليه إلى اليسار."""
    col_w = _index_col_width()
    right_edge = RIGHT_X - col * (col_w + ALLTIMES_INDEX_GUTTER)
    left_edge = right_edge - col_w
    return right_edge, left_edge


def _draw_index_table_header(c, top_y):
    """صف عناوين الجدول (الرقم/المادة/الصفحة) فوق كل عمود، مع خط فاصل
    تحته. يتكرر في أعلى كل صفحة فهرس (وليس فقط الأولى) لأن الجدول قد
    يمتد لعدة صفحات."""
    c.setFont(FONT_NAME_BOLD, 9)
    c.setFillColor(colors.HexColor("#1F3864"))
    for col in range(ALLTIMES_INDEX_COLS):
        right_edge, left_edge = _index_column_x(col)
        c.drawRightString(right_edge, top_y, _ar("الرقم"))
        c.drawRightString(right_edge - ALLTIMES_INDEX_NUM_COL_W - 2 * mm, top_y, _ar("المادة"))
        c.drawString(left_edge + 2 * mm, top_y, _ar("الصفحة"))
    c.setFillColor(colors.black)

    rule_y = top_y - 2.5 * mm
    c.setStrokeColor(colors.HexColor("#1F3864"))
    c.setLineWidth(0.8)
    c.line(LEFT_X, rule_y, RIGHT_X, rule_y)

    # خط فاصل رأسي رفيع بين العمودين
    if ALLTIMES_INDEX_COLS == 2:
        _, left_of_right_col = _index_column_x(0)
        right_of_left_col, _ = _index_column_x(1)
        mid_x = (left_of_right_col + right_of_left_col) / 2
        c.setStrokeColor(colors.HexColor("#D0D7E5"))
        c.setLineWidth(0.5)
        c.line(mid_x, rule_y, mid_x, rule_y - (_index_rows_per_column(False) * ALLTIMES_INDEX_ROW_H))
    c.setStrokeColor(colors.black)


def _draw_index_row(c, row_top_y, col, serial, course, final_start_page):
    right_edge, left_edge = _index_column_x(col)
    name_w = _index_name_col_width()
    text_y = row_top_y - ALLTIMES_INDEX_ROW_H / 2 + 1.2 * mm

    # تظليل متناوب (zebra) لسهولة تتبّع الصفوف بلا خطوط أفقية كثيرة
    if serial % 2 == 0:
        c.setFillColor(colors.HexColor("#F4F7FC"))
        c.rect(left_edge, row_top_y - ALLTIMES_INDEX_ROW_H, right_edge - left_edge, ALLTIMES_INDEX_ROW_H,
               fill=1, stroke=0)

    c.setFont(FONT_NAME, ALLTIMES_INDEX_FONT_SIZE)
    c.setFillColor(colors.HexColor("#1F3864"))
    c.drawRightString(right_edge, text_y, str(serial))

    name_text = _truncate_to_width(_ar(course["name"]), FONT_NAME, ALLTIMES_INDEX_FONT_SIZE, name_w)
    c.setFillColor(colors.black)
    c.drawRightString(right_edge - ALLTIMES_INDEX_NUM_COL_W - 2 * mm, text_y, name_text)

    c.setFillColor(colors.HexColor("#1F3864"))
    c.drawString(left_edge + 2 * mm, text_y, str(final_start_page[course["code"]]))
    c.setFillColor(colors.black)


def _draw_index_pages(c, courses, final_start_page, index_page_count, total_pages):
    total = len(courses)
    page_in_index = 1
    idx = 0

    while idx < total:
        first_page = page_in_index == 1
        _draw_alltimes_scaffold(c, page_in_index, total_pages)
        if first_page:
            _draw_index_title_block(c, total)
            table_top = ALLTIMES_CONTENT_TOP - ALLTIMES_INDEX_TITLE_BLOCK
        else:
            table_top = ALLTIMES_CONTENT_TOP

        _draw_index_table_header(c, table_top)
        rows_top = table_top - ALLTIMES_INDEX_HEADER_H
        rows_per_col = _index_rows_per_column(first_page)
        capacity = rows_per_col * ALLTIMES_INDEX_COLS

        page_courses = courses[idx: idx + capacity]
        for local_i, course in enumerate(page_courses):
            serial = idx + local_i + 1
            col = local_i // rows_per_col
            row = local_i % rows_per_col
            row_top_y = rows_top - row * ALLTIMES_INDEX_ROW_H
            _draw_index_row(c, row_top_y, col, serial, course, final_start_page)

        idx += len(page_courses)
        if idx < total:
            c.showPage()
            page_in_index += 1


def _draw_alltimes_course_header(c, y, course, serial):
    box_h = ALLTIMES_COURSE_HEADER_H - 1.5 * mm
    c.setFillColor(colors.HexColor("#E8F0FE"))
    c.roundRect(LEFT_X, y - box_h, CONTENT_W, box_h, 1.5 * mm, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#1F3864"))
    c.setFont(FONT_NAME_BOLD, 13)
    c.drawRightString(RIGHT_X - 3 * mm, y - 7 * mm, _ar(f"{serial}.  {course['name']}"))
    c.setFont(FONT_NAME, 9)
    c.setFillColor(colors.HexColor("#5B6B87"))
    c.drawString(LEFT_X + 3 * mm, y - 7 * mm, course["code"])
    c.setFillColor(colors.black)
    return y - ALLTIMES_COURSE_HEADER_H


def _draw_alltimes_activity_header(c, y, activity_name):
    label = ACTIVITY_SECTION_LABELS.get(activity_name, activity_name)
    c.setFillColor(colors.HexColor("#1F3864"))
    c.setFont(FONT_NAME_BOLD, 11)
    c.drawRightString(RIGHT_X - 6 * mm, y - 5 * mm, _ar(label))
    c.setStrokeColor(colors.HexColor("#C9DAF8"))
    c.setLineWidth(0.6)
    c.line(LEFT_X + 4 * mm, y - 6.5 * mm, RIGHT_X - 6 * mm, y - 6.5 * mm)
    c.setFillColor(colors.black)
    return y - ALLTIMES_ACTIVITY_HEADER_H


def _draw_alltimes_session_line(c, y, section_id, sessions):
    formatted = []
    for s in sessions:
        formatted.append(f"{s['start']} - {s['end']}  {_ar(s['day'])}")
    days_str = "  |  ".join(formatted)

    room = sessions[0].get("room", "")
    teacher = sessions[0].get("teacher", "")
    detail = f"شعبة {section_id}"
    if room:
        detail += f" | القاعة {room}"
    if teacher:
        detail += f" | {teacher}"

    c.setFont(FONT_NAME, 10)
    c.setFillColor(colors.HexColor("#333333"))
    c.drawRightString(RIGHT_X - 9 * mm, y - 4 * mm, _ar(detail))
    c.setFillColor(colors.HexColor("#555555"))
    c.setFont(FONT_NAME, 9)
    c.drawString(LEFT_X + 5 * mm, y - 4 * mm, days_str)
    c.setFillColor(colors.black)
    return y - ALLTIMES_SESSION_LINE_H


def _draw_body_pages(c, courses, index_page_count, total_pages):
    page_in_body = 1
    _draw_alltimes_scaffold(c, index_page_count + page_in_body, total_pages)
    y = ALLTIMES_CONTENT_TOP

    for i, course in enumerate(courses, start=1):
        groups = _course_activity_groups(course)
        first_chunk = ALLTIMES_COURSE_HEADER_H + ALLTIMES_ACTIVITY_HEADER_H + ALLTIMES_SESSION_LINE_H
        if y - first_chunk < ALLTIMES_BOTTOM_LIMIT:
            c.showPage()
            page_in_body += 1
            _draw_alltimes_scaffold(c, index_page_count + page_in_body, total_pages)
            y = ALLTIMES_CONTENT_TOP

        y = _draw_alltimes_course_header(c, y, course, i)

        for act_name, sections in groups:
            if y - ALLTIMES_ACTIVITY_HEADER_H < ALLTIMES_BOTTOM_LIMIT:
                c.showPage()
                page_in_body += 1
                _draw_alltimes_scaffold(c, index_page_count + page_in_body, total_pages)
                y = ALLTIMES_CONTENT_TOP
            y = _draw_alltimes_activity_header(c, y, act_name)

            for section_id, sess_list in sections:
                if y - ALLTIMES_SESSION_LINE_H < ALLTIMES_BOTTOM_LIMIT:
                    c.showPage()
                    page_in_body += 1
                    _draw_alltimes_scaffold(c, index_page_count + page_in_body, total_pages)
                    y = ALLTIMES_CONTENT_TOP
                y = _draw_alltimes_session_line(c, y, section_id, sess_list)

        y -= ALLTIMES_COURSE_GAP_H


def build_all_times_pdf(years_data, output_path):
    """
    يبني ملف PDF لكل المواد التي جُلبت من موقع الجامعة (بلا تصنيف حسب
    السنة)، مرتَّبة أبجديًا، مع قسم منفصل لكل مادة تحت اسمها لكل نوع
    نشاط (القسم النظري / القسم العملي) إن وُجد. الصفحة الأولى (وما بعدها
    إن لزم) فهرس مرقّم بكل أسماء المواد وأرقام صفحاتها الفعلية، ليتمكن
    القارئ من القفز مباشرة لما يريده. لا يحتوي الملف على اسم WebSeeker
    ولا رأسه المُعلَّم، فقط عنوان نصي بسيط.
    """
    _register_fonts()
    courses = _gather_all_courses_flat(years_data)

    c = canvas.Canvas(output_path, pagesize=A4)

    if not courses:
        _draw_alltimes_scaffold(c, 1, 1)
        c.setFont(FONT_NAME, 13)
        c.drawCentredString(PAGE_W / 2, PAGE_H / 2, _ar("لا توجد بيانات جدول متاحة حاليًا."))
        c.save()
        return

    index_page_count = _compute_index_pagination(courses)
    body_start_pages, body_page_count = _compute_body_pagination(courses)
    total_pages = index_page_count + body_page_count
    final_start_page = {code: index_page_count + p for code, p in body_start_pages.items()}

    _draw_index_pages(c, courses, final_start_page, index_page_count, total_pages)
    c.showPage()
    _draw_body_pages(c, courses, index_page_count, total_pages)
    c.save()
