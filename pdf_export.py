#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
FONT_NAME = "Arabic"
FONT_NAME_BOLD = "Arabic-Bold"

# خطوط العلامة (Brand fonts) الخاصة باسم WebSeeker وعبارة "by yazan alsawadi":
# Orbitron لاسم WebSeeker نفسه (خط مستقبلي واضح يعطي حجمًا وهوية بصرية مميزة)،
# وComfortaa لعبارة "by yazan alsawadi" الأصغر تحته مباشرة. كلا الاسمين
# لاتينيان بالكامل، فلا حاجة لأي تشكيل عربي (arabic_text) عند رسمهما.
ORBITRON_FONT_NAME = "Orbitron"
COMFORTAA_FONT_NAME = "Comfortaa"

_font_registered = False
_orbitron_registered = False
_comfortaa_registered = False


def _register_fonts():
    """يسجّل الخط العربي (الإلزامي لعمل الملف أساسًا) بالإضافة لخطي العلامة
    Orbitron و Comfortaa إن وُجد ملفاهما في مجلد fonts/ (اختياريان تمامًا:
    غيابهما لا يُسقط الملف، فقط يُستخدَم الخط العربي الغامق/العادي كبديل
    مؤقت لاسم العلامة وعبارة "by yazan alsawadi" حتى تُرفَع الملفات
    الصحيحة). يكفي أن يحتوي اسم الملف على كلمة "orbitron" أو "comfortaa"
    (بأي حالة أحرف) ليُكتشف تلقائيًا، دون أي ضبط إضافي مطلوب."""
    global _font_registered, _orbitron_registered, _comfortaa_registered
    if _font_registered:
        return

    regular_path = None
    bold_path = None
    orbitron_path = None
    comfortaa_path = None

    if os.path.isdir(FONTS_DIR):
        for fname in os.listdir(FONTS_DIR):
            lower = fname.lower()
            if not lower.endswith(".ttf"):
                continue
            full = os.path.join(FONTS_DIR, fname)

            if "orbitron" in lower:
                if orbitron_path is None:
                    orbitron_path = full
                continue
            if "comfortaa" in lower:
                if comfortaa_path is None:
                    comfortaa_path = full
                continue

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

    if orbitron_path:
        try:
            pdfmetrics.registerFont(TTFont(ORBITRON_FONT_NAME, orbitron_path))
            _orbitron_registered = True
        except Exception:
            _orbitron_registered = False

    if comfortaa_path:
        try:
            pdfmetrics.registerFont(TTFont(COMFORTAA_FONT_NAME, comfortaa_path))
            _comfortaa_registered = True
        except Exception:
            _comfortaa_registered = False

    _font_registered = True


def brand_font_name():
    """اسم الخط المُستخدم فعليًا لكلمة WebSeeker: Orbitron إن وُجد ملفه في
    fonts/ (بعد استدعاء _register_fonts())، وإلا الخط العربي الغامق كحل
    بديل مؤقت لا يُسقط الملف أبدًا."""
    return ORBITRON_FONT_NAME if _orbitron_registered else FONT_NAME_BOLD


def brand_sub_font_name():
    """اسم الخط المُستخدم فعليًا لعبارة by yazan alsawadi: Comfortaa إن وُجد
    ملفه في fonts/، وإلا الخط العربي العادي كحل بديل مؤقت."""
    return COMFORTAA_FONT_NAME if _comfortaa_registered else FONT_NAME


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
# تصميم احترافي ملوّن لكنه اقتصادي بالحبر: بلا أي تدرّج لوني (كل لون هنا
# لون صلب واحد مستقل، لا يندرج ضمن مقياس من الغامق للفاتح)، وبلا تلوين
# خلفيات كاملة كبيرة -- الألوان تُستخدَم كلمسات (دوائر صغيرة، أشرطة جانبية
# رفيعة، شارات وقت) لا كخلفيات مشبعة، فتبقى الطباعة عملية واقتصادية.
HEADER_HEIGHT = 16 * mm
BRAND_NAME_SIZE = 14
BRAND_TEXT = "WebSeeker"
BRAND_ACCENT = "#123A5C"

DAY_COLORS = {
    "السبت": "#0F4C5C",
    "الأحد": "#9A031E",
    "الاثنين": "#1B4332",
    "الثلاثاء": "#5B2C6F",
    "الأربعاء": "#B5651D",
    "الخميس": "#14213D",
    "الجمعة": "#6B4226",
}
DAY_COLOR_DEFAULT = "#333333"


def _day_color(day):
    return colors.HexColor(DAY_COLORS.get(day, DAY_COLOR_DEFAULT))


def _tint(color_obj, factor=0.88):
    """يُخفّف لونًا صلبًا نحو الأبيض (خلط ألوان حقيقي، وليس شفافية) لإنتاج
    نسخة فاتحة جدًا منه تصلح كخلفية بطاقة/شارة خفيفة الحبر عند الطباعة."""
    r = color_obj.red + (1 - color_obj.red) * factor
    g = color_obj.green + (1 - color_obj.green) * factor
    b = color_obj.blue + (1 - color_obj.blue) * factor
    return colors.Color(r, g, b)


def _new_page(c):
    c.showPage()
    _draw_header(c)
    return PAGE_H - HEADER_HEIGHT - 8 * mm


def _draw_header(c):
    """رأس بسيط وأنيق: كلمة WebSeeker بخط Orbitron (أو الخط العربي الغامق
    كحل بديل إن لم يُرفَع ملف الخط بعد) أعلى اليسار، مع عبارة
    'by yazan alsawadi' بخط Comfortaa تحتها مباشرة بحجم أصغر واضح، ونقطة
    صغيرة بلون العلامة، وخط رفيع بلون العلامة يفصل الرأس عن المحتوى."""
    brand_font = brand_font_name()
    sub_font = brand_sub_font_name()

    c.setFillColor(colors.black)
    c.setFont(brand_font, BRAND_NAME_SIZE)
    text_baseline_y = PAGE_H - 10 * mm
    c.drawString(LEFT_X, text_baseline_y, BRAND_TEXT)

    brand_w = pdfmetrics.stringWidth(BRAND_TEXT, brand_font, BRAND_NAME_SIZE)
    c.setFillColor(colors.HexColor(BRAND_ACCENT))
    c.circle(LEFT_X + brand_w + 3 * mm, text_baseline_y + 1.6 * mm, 1.1 * mm, fill=1, stroke=0)

    sub_size = BRAND_NAME_SIZE * 0.42
    c.setFillColor(colors.HexColor("#666666"))
    c.setFont(sub_font, sub_size)
    c.drawString(LEFT_X, text_baseline_y - BRAND_NAME_SIZE * 0.62, "by yazan alsawadi")

    rule_y = PAGE_H - HEADER_HEIGHT
    c.setStrokeColor(colors.HexColor(BRAND_ACCENT))
    c.setLineWidth(1)
    c.line(LEFT_X, rule_y, RIGHT_X, rule_y)
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


def _draw_time_chip(c, x_left, y_top, text, color, font_size=9):
    """شارة وقت صغيرة ملوّنة (مساحة حبر محدودة جدًا) بدل تلوين خلفية
    البطاقة كاملة -- لمسة أنيقة اقتصادية بالحبر."""
    height = 5.6 * mm
    pad = 2.6 * mm
    w = pdfmetrics.stringWidth(text, FONT_NAME_BOLD, font_size) + 2 * pad
    y_bottom = y_top - height
    c.setFillColor(color)
    c.roundRect(x_left, y_bottom, w, height, height / 2, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(FONT_NAME_BOLD, font_size)
    c.drawCentredString(x_left + w / 2, y_bottom + height / 2 - font_size * 0.32, text)
    c.setFillColor(colors.black)
    return w


def _draw_intro_banner(c, y, lines_with_sizes):
    """صندوق ملخّص علوي بخلفية فاتحة جدًا بلون العلامة (تخفيف حقيقي نحو
    الأبيض، لا تشبّع كامل ولا شفافية) يُبرز إحصائيات الجدول المثالي بشكل
    أنيق، بدل أسطر نص معزولة على خلفية بيضاء عادية."""
    if not lines_with_sizes:
        return y

    accent = colors.HexColor(BRAND_ACCENT)
    font_size = lines_with_sizes[0][1]
    max_text_width = CONTENT_W - 12 * mm

    wrapped_lines = []
    for text, size in lines_with_sizes:
        wrapped_lines.extend(_wrap_text_to_width(c, _ar(text), FONT_NAME_BOLD, size, max_text_width))

    line_height = font_size * 1.55
    banner_h = len(wrapped_lines) * line_height + 7 * mm
    banner_bottom = y - banner_h

    c.setFillColor(_tint(accent, 0.90))
    c.roundRect(LEFT_X, banner_bottom, CONTENT_W, banner_h, 2.5 * mm, fill=1, stroke=0)
    c.setStrokeColor(accent)
    c.setLineWidth(0.8)
    c.roundRect(LEFT_X, banner_bottom, CONTENT_W, banner_h, 2.5 * mm, fill=0, stroke=1)

    text_y = y - 5.3 * mm
    c.setFont(FONT_NAME_BOLD, font_size)
    c.setFillColor(accent)
    for line in wrapped_lines:
        c.drawCentredString(PAGE_W / 2, text_y, line)
        text_y -= line_height

    c.setFillColor(colors.black)
    return banner_bottom - 6 * mm


def _draw_day_header(c, y, day, day_num, day_color):
    """رأس يوم أنيق واقتصادي بالحبر: دائرة صغيرة ملوّنة برقم تسلسلي بدل
    شريط أسود كامل العرض يستهلك حبرًا كثيرًا، مع اسم اليوم بخط ملوّن غامق،
    وخط رفيع ملوّن يفصل رأس اليوم عن حصصه."""
    header_top = y
    circle_r = 3.6 * mm
    circle_cx = RIGHT_X - circle_r
    circle_cy = header_top - 6 * mm

    c.setFillColor(day_color)
    c.circle(circle_cx, circle_cy, circle_r, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(FONT_NAME_BOLD, 9)
    c.drawCentredString(circle_cx, circle_cy - 3, str(day_num))

    c.setFillColor(day_color)
    c.setFont(FONT_NAME_BOLD, DAY_HEADER_SIZE)
    c.drawRightString(circle_cx - circle_r - 3 * mm, circle_cy - 3, _ar(day))

    rule_y = header_top - 10 * mm
    c.setStrokeColor(day_color)
    c.setLineWidth(1.3)
    c.line(LEFT_X, rule_y, RIGHT_X, rule_y)

    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)
    return header_top - 13 * mm


def _draw_session_card(c, y, name, s, day_color, entry_idx):
    """بطاقة حصة أنيقة واقتصادية بالحبر: شريط لوني رفيع جانبي (٣مم فقط)
    يربطها بصريًا بيوم معيّن بدل تلوين البطاقة كاملة، وشارة وقت ملوّنة
    صغيرة، وإطار رفيع رمادي (لا أسود صلب) حول البطاقة."""
    card_top = y
    card_h = 13 * mm
    card_bottom = card_top - card_h
    stripe_w = 3 * mm

    if entry_idx % 2 == 0:
        c.setFillColor(colors.HexColor("#F7F7F7"))
        c.rect(LEFT_X, card_bottom, CONTENT_W, card_h, fill=1, stroke=0)

    c.setFillColor(day_color)
    c.rect(RIGHT_X - stripe_w, card_bottom, stripe_w, card_h, fill=1, stroke=0)

    c.setStrokeColor(colors.HexColor("#BFBFBF"))
    c.setLineWidth(0.6)
    c.rect(LEFT_X, card_bottom, CONTENT_W, card_h, fill=0, stroke=1)

    name_right_edge = RIGHT_X - stripe_w - 3 * mm
    c.setFillColor(colors.black)
    c.setFont(FONT_NAME_BOLD, COURSE_NAME_SIZE)
    c.drawRightString(name_right_edge, card_top - 5.4 * mm, _ar(name))

    activity = s["activity"]
    time_range = f"{s['start']} - {s['end']}"
    room = s["room"]
    teacher = s["teacher"]
    _draw_time_chip(c, LEFT_X + 3 * mm, card_top - 3.4 * mm, time_range, day_color)

    detail_parts = [activity]
    if room:
        detail_parts.append(f"القاعة: {room}")
    if teacher:
        detail_parts.append(teacher)
    detail_text = "  |  ".join(detail_parts)
    c.setFont(FONT_NAME, DETAIL_SIZE - 1)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawRightString(name_right_edge, card_top - 11 * mm, _ar(detail_text))

    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)
    return card_bottom - 3 * mm


def _draw_sessions_body(c, y, all_sessions, extra_intro_lines=None):
    if extra_intro_lines:
        y = _draw_intro_banner(c, y, extra_intro_lines)

    by_day = {}
    for day, start_min, name, s in all_sessions:
        by_day.setdefault(day, []).append((start_min, name, s))
    ordered_days = [d for d in sd.DAY_ORDER if d in by_day]
    if not ordered_days:
        c.setFont(FONT_NAME, DETAIL_SIZE + 2)
        c.setFillColor(colors.black)
        c.drawCentredString(PAGE_W / 2, y, _ar("لا توجد معلومات جدول للمواد المختارة."))
        return y

    # كل يوم يأخذ لونًا صلبًا مستقلًا خاصًا به (لا تدرّج بين الأيام)، تُستخدم
    # الألوان كلمسات صغيرة فقط (دائرة الرقم، الخط الفاصل، الشريط الجانبي،
    # شارة الوقت) بدل تلوين خلفيات كاملة، فتبقى الطباعة عملية واقتصادية.
    entry_idx = 0
    for day_num, day in enumerate(ordered_days, start=1):
        sessions_today = sorted(by_day[day], key=lambda x: x[0])
        needed_height = 14 * mm + len(sessions_today) * 16 * mm
        if y - needed_height < MARGIN:
            y = _new_page(c)

        day_color = _day_color(day)
        y = _draw_day_header(c, y, day, day_num, day_color)

        for _, name, s in sessions_today:
            if y - 16 * mm < MARGIN:
                y = _new_page(c)
            y = _draw_session_card(c, y, name, s, day_color, entry_idx)
            entry_idx += 1
        y -= 4 * mm
    return y


def _draw_footer(c):
    c.setStrokeColor(colors.HexColor(BRAND_ACCENT))
    c.setLineWidth(0.6)
    c.line(LEFT_X, MARGIN, RIGHT_X, MARGIN)
    c.setFont(FONT_NAME, 8)
    c.setFillColor(colors.HexColor("#888888"))
    c.drawCentredString(PAGE_W / 2, MARGIN / 2, _ar("تم إنشاء هذا الجدول تلقائيًا بواسطة بوت جدول IUST"))


def build_schedule_pdf(years_data, selected_list, output_path, student_name=None):
    """ملف "عرض الجدول" (المواد التي اختارها الطالب): التصميم الكحلي الجديد
    موجود في selected_schedule_pdf.py. الاستيراد هنا داخل الدالة عمدًا لتفادي
    الاستيراد الدائري بين الملفين."""
    import selected_schedule_pdf
    selected_schedule_pdf.build_schedule_pdf(years_data, selected_list, output_path, student_name)


def build_optimized_schedule_pdf(chosen_options, output_path, stats_lines=None):
    """ملف "الجدول المثالي": التصميم الكحلي الجديد في selected_schedule_pdf.py."""
    import selected_schedule_pdf
    selected_schedule_pdf.build_optimized_schedule_pdf(chosen_options, output_path, stats_lines)


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
