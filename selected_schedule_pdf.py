#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selected_schedule_pdf.py
-------------------------
تصميم ملف PDF الخاص بـ "جدول المواد التي اختارها الطالب" فقط (زر: عرض الجدول).

هذا الملف مستقل عمدًا عن بقية أنواع ملفات PDF في pdf_export.py:
    - ملف "أوقات جميع المواد"   -> لم يتغيّر إطلاقًا.
    - ملف "الجدول المثالي"      -> لم يتغيّر إطلاقًا (ما زال يستخدم رسم pdf_export القديم).

الهوية البصرية: كحلي (Navy) + لمسة ذهبية رفيعة جدًا. النظري بالكحلي الغامق،
والعملي بأزرق أفتح، ليُميَّز النوع بصريًا من النظرة الأولى.

اسم العلامة: كلمة "WebSeeker" بخط Orbitron بحجم واضح (أو الخط العربي
الغامق كحل بديل إن لم يُرفَع ملف الخط بعد، انظر pdf_export.brand_font_name)،
مع عبارة "by yazan alsawadi" بخط Comfortaa تحتها مباشرة بحجم أصغر دائمًا
(نسبته ثابتة من حجم اسم العلامة)، في كل مكان تظهر فيه العلامة: الغلاف
وترويسة كل صفحة لاحقة.

عناصر التصميم:
    1) غلاف علوي كحلي بأشكال هندسية خفيفة، مع بطاقات إحصائية عائمة.
    2) خريطة أسبوعية (Timeline) تُظهر كل الأيام والساعات في نظرة واحدة.
    3) رأس لكل يوم بشريط كحلي (بدون أي ترقيم).
    4) بطاقات الحصص: اسم المادة ثم وقتها ملاصق له مباشرة، وتحتها شارات
       صغيرة (النوع، الشعبة، القاعة، المدرّس).
    5) تذييل بترقيم الصفحات (1 / N).
"""

from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

import schedule_data as sd
import pdf_export as pe

# ---------------------------------------------------------------------------
# الألوان (كلها ألوان صلبة، بلا تدرّجات)
# ---------------------------------------------------------------------------
NAVY_DARK = colors.HexColor("#0A1F44")   # الغلاف + النصوص الرئيسية
NAVY = colors.HexColor("#13315C")        # النظري + رؤوس الأيام
BLUE = colors.HexColor("#3A6EA5")        # العملي
RING = colors.HexColor("#24467F")        # حلقة زخرفية خفيفة في الغلاف
TINT = colors.HexColor("#EAF0F9")        # خلفية الشارات
TINT_SOFT = colors.HexColor("#F5F8FC")   # خلفية صفوف الخريطة المتناوبة
LINE = colors.HexColor("#B0C1DA")        # خطوط الشبكة الداخلية
BORDER = colors.HexColor("#6F89B3")      # حدود البطاقات والجداول (واضحة على الخلفية)
SHADOW = colors.HexColor("#C3CEE2")      # ظل البطاقات
MUTED = colors.HexColor("#5A6B85")       # نصوص ثانوية
LIGHT_TEXT = colors.HexColor("#B9CCE8")  # نص فاتح فوق الكحلي
GOLD = colors.HexColor("#C9A227")        # لمسة الهوية (خطوط رفيعة فقط)

# ---------------------------------------------------------------------------
# القياسات
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = A4
MARGIN = 15 * mm
LEFT_X = MARGIN
RIGHT_X = PAGE_W - MARGIN
CONTENT_W = PAGE_W - 2 * MARGIN

HERO_H = 52 * mm
# ارتفاع ترويسة الصفحات اللاحقة: زيدَ من 15mm إلى 18mm ليتّسع بشكل مريح
# لسطر "by yazan alsawadi" الإضافي تحت WebSeeker دون أي تلامس مع حافة
# الترويسة السفلية.
SLIM_H = 18 * mm
BOTTOM_LIMIT = 17 * mm
CARD_H = 17 * mm
CARD_GAP = 3.2 * mm
DAY_HEADER_H = 10 * mm

BRAND = "WebSeeker"
BRAND_SUB = "by yazan alsawadi"
TITLE = "الجدول الدراسي الأسبوعي"
SUBTITLE = "جدول مخصص للمواد التي اخترتها"
OPT_TITLE = "الجدول المثالي المقترح"
OPT_SUBTITLE = "أفضل توزيع للشعب بأقل عدد أيام وأقل فراغات"

OVERVIEW_MAX_H = 100 * mm
OVERVIEW_LANE_HEIGHTS = (6.2 * mm, 5.4 * mm, 4.6 * mm)


def _ar(text):
    return pe._ar(text)


def _activity_color(activity):
    return BLUE if activity == "عملي" else NAVY


# ---------------------------------------------------------------------------
# أدوات نصية (القصّ والالتفاف يتمّان على النص المنطقي قبل التشكيل، حتى لا
# يُقصّ الجزء الخطأ من الكلمة العربية)
# ---------------------------------------------------------------------------

def _fit(text, font_name, font_size, max_width):
    """يعيد نصًا عربيًا جاهزًا للرسم، مقصوصًا بـ '…' إن لزم ليتّسع في max_width."""
    text = str(text or "").strip()
    if max_width <= 0 or not text:
        return ""
    if pdfmetrics.stringWidth(_ar(text), font_name, font_size) <= max_width:
        return _ar(text)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid].rstrip() + "…"
        if pdfmetrics.stringWidth(_ar(candidate), font_name, font_size) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    if lo == 0:
        return ""
    return _ar(text[:lo].rstrip() + "…")


def _wrap(text, font_name, font_size, max_width):
    """يلفّ نصًا منطقيًا على عدة أسطر، ويعيد الأسطر جاهزة للرسم."""
    lines, current = [], ""
    for word in str(text).split():
        candidate = f"{current} {word}".strip()
        if not current or pdfmetrics.stringWidth(_ar(candidate), font_name, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return [_ar(line) for line in lines]


def _pill(c, x_right, y_center, text, font_name, font_size, fill, text_color,
          pad=2.4 * mm, height=5.2 * mm):
    """شارة مستديرة صغيرة، حافتها اليمنى عند x_right. تعيد عرضها."""
    width = pdfmetrics.stringWidth(text, font_name, font_size) + 2 * pad
    c.setFillColor(fill)
    c.roundRect(x_right - width, y_center - height / 2, width, height, height / 2, fill=1, stroke=0)
    c.setFillColor(text_color)
    c.setFont(font_name, font_size)
    c.drawCentredString(x_right - width / 2, y_center - font_size * 0.32, text)
    return width


def _hour_label(hour):
    hour = hour % 24
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12} {suffix}"


def _draw_brand(c, x_left, baseline_y, size, color=colors.white, sub_color=None):
    """يرسم اسم العلامة WebSeeker بخط Orbitron (أو الخط العربي الغامق كحل
    بديل مؤقت إن لم يُرفَع ملف الخط بعد -- انظر pdf_export.brand_font_name)
    بحجم `size` الواضح، مع عبارة "by yazan alsawadi" بخط Comfortaa (أو
    الخط العربي العادي كحل بديل) مباشرة تحته، بحجم أصغر دائمًا (42% من حجم
    اسم العلامة تقريبًا) لضمان أن WebSeeker يبقى العنصر الأبرز بصريًا."""
    brand_font = pe.brand_font_name()
    c.setFillColor(color)
    c.setFont(brand_font, size)
    c.drawString(x_left, baseline_y, BRAND)
    w = pdfmetrics.stringWidth(BRAND, brand_font, size)

    c.setFillColor(GOLD)
    c.circle(x_left + w + 2.6 * mm, baseline_y + size * 0.32, 1.05 * mm, fill=1, stroke=0)

    sub_font = pe.brand_sub_font_name()
    sub_size = size * 0.42
    c.setFillColor(sub_color if sub_color is not None else LIGHT_TEXT)
    c.setFont(sub_font, sub_size)
    c.drawString(x_left, baseline_y - size * 0.62, BRAND_SUB)


# ---------------------------------------------------------------------------
# الغلاف والرأس
# ---------------------------------------------------------------------------

def _draw_hero(c, stats):
    band_bottom = PAGE_H - HERO_H

    c.setFillColor(NAVY_DARK)
    c.rect(0, band_bottom, PAGE_W, HERO_H, fill=1, stroke=0)

    # أشكال هندسية خفيفة (تُقصّ داخل حدود الغلاف)
    c.saveState()
    clip = c.beginPath()
    clip.rect(0, band_bottom, PAGE_W, HERO_H)
    c.clipPath(clip, stroke=0, fill=0)
    c.setFillColor(NAVY)
    c.circle(PAGE_W - 12 * mm, PAGE_H - 4 * mm, 40 * mm, fill=1, stroke=0)
    c.setStrokeColor(RING)
    c.setLineWidth(0.8)
    c.circle(PAGE_W - 12 * mm, PAGE_H - 4 * mm, 52 * mm, fill=0, stroke=1)
    c.setFillColor(NAVY)
    c.circle(20 * mm, band_bottom - 4 * mm, 24 * mm, fill=1, stroke=0)
    c.restoreState()

    # خط ذهبي رفيع أسفل الغلاف
    c.setFillColor(GOLD)
    c.rect(0, band_bottom - 1.2 * mm, PAGE_W, 1.2 * mm, fill=1, stroke=0)

    _draw_brand(c, LEFT_X, PAGE_H - 14 * mm, 16)

    date_text = datetime.now().strftime("%Y-%m-%d")
    c.setFillColor(LIGHT_TEXT)
    c.setFont(pe.FONT_NAME, 9)
    c.drawRightString(RIGHT_X, PAGE_H - 13.5 * mm, _ar(f"تاريخ الإنشاء: {date_text}"))

    c.setFillColor(colors.white)
    c.setFont(pe.FONT_NAME_BOLD, 25)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 28 * mm, _ar(getattr(c, "_ws_title", TITLE)))

    c.setFillColor(GOLD)
    c.rect(PAGE_W / 2 - 9 * mm, PAGE_H - 32 * mm, 18 * mm, 0.9 * mm, fill=1, stroke=0)

    c.setFillColor(LIGHT_TEXT)
    c.setFont(pe.FONT_NAME, 10.5)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 38 * mm, _ar(getattr(c, "_ws_subtitle", SUBTITLE)))

    _draw_stat_cards(c, band_bottom, stats)


def _draw_stat_cards(c, band_bottom, stats):
    gap = 5 * mm
    width = (CONTENT_W - 2 * gap) / 3
    height = 17 * mm
    top = band_bottom + 8 * mm
    bottom = top - height

    items = [
        ("المواد", stats["courses"]),
        ("الحصص", stats["sessions"]),
        ("أيام الدراسة", stats["days"]),
    ]
    for i, (label, value) in enumerate(items):
        x_right = RIGHT_X - i * (width + gap)
        x_left = x_right - width
        cx = (x_left + x_right) / 2

        c.setFillColor(SHADOW)
        c.roundRect(x_left + 0.7 * mm, bottom - 0.7 * mm, width, height, 2.2 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setStrokeColor(BORDER)
        c.setLineWidth(1.0)
        c.roundRect(x_left, bottom, width, height, 2.2 * mm, fill=1, stroke=1)

        c.setFillColor(GOLD)
        c.rect(cx - 5 * mm, top - 0.9 * mm, 10 * mm, 0.9 * mm, fill=1, stroke=0)

        c.setFillColor(NAVY_DARK)
        c.setFont(pe.FONT_NAME_BOLD, 17)
        c.drawCentredString(cx, top - 8.6 * mm, str(value))
        c.setFillColor(MUTED)
        c.setFont(pe.FONT_NAME, 9)
        c.drawCentredString(cx, top - 13.6 * mm, _ar(label))


def _draw_slim_header(c):
    band_bottom = PAGE_H - SLIM_H
    c.setFillColor(NAVY_DARK)
    c.rect(0, band_bottom, PAGE_W, SLIM_H, fill=1, stroke=0)
    c.setFillColor(GOLD)
    c.rect(0, band_bottom - 0.9 * mm, PAGE_W, 0.9 * mm, fill=1, stroke=0)
    _draw_brand(c, LEFT_X, PAGE_H - 9.6 * mm, 12)
    c.setFillColor(colors.white)
    c.setFont(pe.FONT_NAME_BOLD, 11)
    c.drawRightString(RIGHT_X, PAGE_H - 9.4 * mm, _ar(getattr(c, "_ws_title", TITLE)))


def _new_page(c):
    c.showPage()
    _draw_slim_header(c)
    return PAGE_H - SLIM_H - 9 * mm


def _draw_section_title(c, y, text, legend=False):
    c.setFillColor(NAVY)
    c.rect(RIGHT_X - 1.6 * mm, y - 6.2 * mm, 1.6 * mm, 6.2 * mm, fill=1, stroke=0)
    c.setFillColor(NAVY_DARK)
    c.setFont(pe.FONT_NAME_BOLD, 12.5)
    c.drawRightString(RIGHT_X - 4.5 * mm, y - 4.8 * mm, _ar(text))

    if legend:
        x = LEFT_X + 42 * mm
        for label, col in (("نظري", NAVY), ("عملي", BLUE)):
            c.setFillColor(col)
            c.roundRect(x - 3.2 * mm, y - 4.7 * mm, 3.2 * mm, 3.2 * mm, 0.8 * mm, fill=1, stroke=0)
            x -= 3.2 * mm + 1.8 * mm
            text_prepared = _ar(label)
            c.setFillColor(MUTED)
            c.setFont(pe.FONT_NAME, 8.5)
            c.drawRightString(x, y - 4.4 * mm, text_prepared)
            x -= pdfmetrics.stringWidth(text_prepared, pe.FONT_NAME, 8.5) + 5 * mm
    return y - 10 * mm


# ---------------------------------------------------------------------------
# الخريطة الأسبوعية (Timeline)
# ---------------------------------------------------------------------------

def _build_overview_rows(sessions, lane_h):
    """يوزّع حصص كل يوم على "مسارات" (lanes) بحيث لا تتراكب الكتل المتداخلة
    زمنيًا فوق بعضها. تُعيد (الصفوف، الارتفاع الكلي)."""
    by_day = {}
    for s in sessions:
        by_day.setdefault(s["day"], []).append(s)

    rows, total = [], 0
    for day in [d for d in sd.DAY_ORDER if d in by_day]:
        items = sorted(by_day[day], key=lambda s: (s["start_min"], s["end_min"]))
        lane_ends, placed = [], []
        for item in items:
            lane = next((i for i, end in enumerate(lane_ends) if end <= item["start_min"]), None)
            if lane is None:
                lane_ends.append(item["end_min"])
                lane = len(lane_ends) - 1
            else:
                lane_ends[lane] = item["end_min"]
            placed.append((lane, item))
        row_h = len(lane_ends) * lane_h + 2.4 * mm
        rows.append((day, placed, row_h))
        total += row_h
    return rows, total


def _plan_overview(sessions):
    """يختار أكبر ارتفاع مسار يجعل الخريطة تتسع في OVERVIEW_MAX_H. تعيد None
    إن لم تتسع (كثرة الشُعب المتوازية) فتُحذف الخريطة ويبقى باقي الملف."""
    if not sessions:
        return None
    header_h = 6.5 * mm
    for lane_h in OVERVIEW_LANE_HEIGHTS:
        rows, body_h = _build_overview_rows(sessions, lane_h)
        if header_h + body_h <= OVERVIEW_MAX_H:
            h0 = min(s["start_min"] for s in sessions) // 60
            h1 = -(-max(s["end_min"] for s in sessions) // 60)
            h1 = min(24, max(h1, h0 + 4))
            return {"rows": rows, "lane_h": lane_h, "header_h": header_h,
                    "body_h": body_h, "h0": h0, "h1": h1}
    return None


def _draw_overview(c, y, plan):
    rows, lane_h = plan["rows"], plan["lane_h"]
    header_h, body_h = plan["header_h"], plan["body_h"]
    h0, h1 = plan["h0"], plan["h1"]

    label_w = 24 * mm
    plot_right = RIGHT_X - label_w
    plot_left = LEFT_X + 6 * mm
    plot_w = plot_right - plot_left
    span_min = (h1 - h0) * 60

    def x_of(minute):
        return plot_right - (minute - h0 * 60) / span_min * plot_w

    total_h = header_h + body_h
    bottom = y - total_h

    # الحاوية المستديرة (كل ما بداخلها يُقصّ على حدودها)
    c.saveState()
    clip = c.beginPath()
    clip.roundRect(LEFT_X, bottom, CONTENT_W, total_h, 2.5 * mm)
    c.clipPath(clip, stroke=0, fill=0)

    c.setFillColor(colors.white)
    c.rect(LEFT_X, bottom, CONTENT_W, total_h, fill=1, stroke=0)
    c.setFillColor(TINT)
    c.rect(LEFT_X, y - header_h, CONTENT_W, header_h, fill=1, stroke=0)

    row_top = y - header_h
    for i, (_day, _placed, row_h) in enumerate(rows):
        if i % 2 == 1:
            c.setFillColor(TINT_SOFT)
            c.rect(LEFT_X, row_top - row_h, CONTENT_W, row_h, fill=1, stroke=0)
        row_top -= row_h

    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    for hour in range(h0, h1 + 1):
        x = x_of(hour * 60)
        c.line(x, y - header_h, x, bottom)
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.7)
    sep_y = y - header_h
    c.line(LEFT_X, sep_y, RIGHT_X, sep_y)
    for _day, _placed, row_h in rows[:-1]:
        sep_y -= row_h
        c.line(LEFT_X, sep_y, RIGHT_X, sep_y)
    c.restoreState()

    # ترويسة الساعات
    c.setFillColor(MUTED)
    c.setFont(pe.FONT_NAME, 6.8)
    for hour in range(h0, h1 + 1):
        c.drawCentredString(x_of(hour * 60), y - header_h / 2 - 2.2, _hour_label(hour))

    # الأيام والكتل
    row_top = y - header_h
    for day, placed, row_h in rows:
        c.setFillColor(NAVY_DARK)
        c.setFont(pe.FONT_NAME_BOLD, 9.5)
        c.drawRightString(RIGHT_X - 3.5 * mm, row_top - row_h / 2 - 3, _ar(day))

        for lane, item in placed:
            right = x_of(item["start_min"])
            left = x_of(item["end_min"])
            width = right - left - 0.5 * mm
            block_top = row_top - 1.2 * mm - lane * lane_h
            block_h = lane_h - 0.9 * mm
            c.setFillColor(_activity_color(item["activity"]))
            c.roundRect(left + 0.25 * mm, block_top - block_h, width, block_h, 1.2 * mm, fill=1, stroke=0)
            if width > 11 * mm:
                label = _fit(item["name"], pe.FONT_NAME_BOLD, 6.4, width - 2.6 * mm)
                if label:
                    c.setFillColor(colors.white)
                    c.setFont(pe.FONT_NAME_BOLD, 6.4)
                    c.drawCentredString(left + 0.25 * mm + width / 2,
                                        block_top - block_h / 2 - 6.4 * 0.32, label)
        row_top -= row_h

    c.setStrokeColor(BORDER)
    c.setLineWidth(1.1)
    c.roundRect(LEFT_X, bottom, CONTENT_W, total_h, 2.5 * mm, fill=0, stroke=1)
    return bottom - 8 * mm


# ---------------------------------------------------------------------------
# رؤوس الأيام وبطاقات الحصص
# ---------------------------------------------------------------------------

def _draw_day_header(c, y, day_text, span_text):
    h = DAY_HEADER_H
    c.setFillColor(NAVY)
    c.roundRect(LEFT_X, y - h, CONTENT_W, h, 2.2 * mm, fill=1, stroke=0)

    c.setFillColor(GOLD)
    c.circle(RIGHT_X - 5 * mm, y - h / 2, 1.3 * mm, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont(pe.FONT_NAME_BOLD, 13)
    c.drawRightString(RIGHT_X - 9.5 * mm, y - h / 2 - 13 * 0.33, day_text)

    if span_text:
        c.setFillColor(LIGHT_TEXT)
        c.setFont(pe.FONT_NAME, 8.5)
        c.drawString(LEFT_X + 4.5 * mm, y - h / 2 - 8.5 * 0.33, span_text)
    return y - h - 3.5 * mm


def _draw_card(c, y, s):
    h = CARD_H
    bottom = y - h
    color = _activity_color(s["activity"])
    font, font_b = pe.FONT_NAME, pe.FONT_NAME_BOLD

    # ظل + جسم البطاقة
    c.setFillColor(SHADOW)
    c.roundRect(LEFT_X + 0.9 * mm, bottom - 0.9 * mm, CONTENT_W, h, 2 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setStrokeColor(BORDER)
    c.setLineWidth(1.0)
    c.roundRect(LEFT_X, bottom, CONTENT_W, h, 2 * mm, fill=1, stroke=1)

    # شريط جانبي بلون النشاط (مقصوص على زوايا البطاقة المستديرة)
    c.saveState()
    clip = c.beginPath()
    clip.roundRect(LEFT_X, bottom, CONTENT_W, h, 2 * mm)
    c.clipPath(clip, stroke=0, fill=0)
    c.setFillColor(color)
    c.rect(RIGHT_X - 2.8 * mm, bottom, 2.8 * mm, h, fill=1, stroke=0)
    c.restoreState()

    name_right = RIGHT_X - 6 * mm
    left_limit = LEFT_X + 4 * mm

    # السطر الأول: اسم المادة، ووقتها ملاصق له مباشرة على يساره
    name_size, time_size = 12.5, 8.6
    baseline = y - 7.4 * mm
    time_text = f"{s['start']} - {s['end']}" if s["start"] and s["end"] else ""
    chip_w = (pdfmetrics.stringWidth(time_text, font_b, time_size) + 5.4 * mm) if time_text else 0
    chip_gap = 4 * mm if time_text else 0

    name_text = _fit(s["name"], font_b, name_size, name_right - left_limit - chip_w - chip_gap)
    c.setFillColor(NAVY_DARK)
    c.setFont(font_b, name_size)
    c.drawRightString(name_right, baseline, name_text)
    name_w = pdfmetrics.stringWidth(name_text, font_b, name_size)

    if time_text:
        _pill(c, name_right - name_w - chip_gap, baseline + name_size * 0.30, time_text,
              font_b, time_size, color, colors.white, pad=2.7 * mm, height=5.8 * mm)

    # السطر الثاني: شارات التفاصيل
    y2 = y - 13.4 * mm
    gap = 2 * mm
    x = name_right
    if s["activity"]:
        x -= _pill(c, x, y2, _ar(s["activity"]), font_b, 8, color, colors.white) + gap
    if s["section"]:
        x -= _pill(c, x, y2, _ar(f"شعبة {s['section']}"), font, 8, TINT, NAVY_DARK) + gap
    if s["room"]:
        text = _fit(f"القاعة: {s['room']}", font, 8, x - left_limit - 4.8 * mm)
        if text:
            x -= _pill(c, x, y2, text, font, 8, TINT, NAVY_DARK) + gap
    if s["teacher"]:
        text = _fit(s["teacher"], font, 8, x - left_limit - 4.8 * mm)
        if text:
            _pill(c, x, y2, text, font, 8, TINT, NAVY_DARK)

    return bottom - CARD_GAP


def _draw_days(c, y, sessions):
    by_day = {}
    for s in sessions:
        if s["day"] in sd.DAY_ORDER:
            by_day.setdefault(s["day"], []).append(s)

    for day in [d for d in sd.DAY_ORDER if d in by_day]:
        items = sorted(by_day[day], key=lambda s: (s["start_min"], s["name"]))

        timed = [s for s in items if s["start"] and s["end"]]
        span_text = ""
        if timed:
            first = min(timed, key=lambda s: s["start_min"])
            last = max(timed, key=lambda s: s["end_min"])
            span_text = f"{first['start']} - {last['end']}"

        if y - (DAY_HEADER_H + 3.5 * mm + CARD_H) < BOTTOM_LIMIT:
            y = _new_page(c)
        y = _draw_day_header(c, y, _ar(day), span_text)

        for s in items:
            if y - CARD_H < BOTTOM_LIMIT:
                y = _new_page(c)
                y = _draw_day_header(c, y, _ar(f"{day} (تابع)"), span_text)
            y = _draw_card(c, y, s)
        y -= 3 * mm
    return y


def _draw_empty_message(c, y):
    h = 18 * mm
    c.setFillColor(TINT)
    c.setStrokeColor(BORDER)
    c.setLineWidth(1.0)
    c.roundRect(LEFT_X, y - h, CONTENT_W, h, 2.5 * mm, fill=1, stroke=1)
    c.setFillColor(NAVY_DARK)
    c.setFont(pe.FONT_NAME_BOLD, 12)
    c.drawCentredString(PAGE_W / 2, y - h / 2 - 4, _ar("لا توجد معلومات جدول للمواد المختارة."))
    return y - h - 6 * mm


def _draw_missing_note(c, y, names):
    """صندوق تنبيه بالمواد المختارة التي لا تتوفر لها أوقات بعد."""
    inner_w = CONTENT_W - 12 * mm
    lines = _wrap("،  ".join(names), pe.FONT_NAME, 9.5, inner_w)
    line_h = 5.2 * mm
    h = 12 * mm + len(lines) * line_h
    if y - h < BOTTOM_LIMIT:
        y = _new_page(c)

    c.setFillColor(TINT_SOFT)
    c.setStrokeColor(BORDER)
    c.setLineWidth(1.0)
    c.roundRect(LEFT_X, y - h, CONTENT_W, h, 2.5 * mm, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.rect(RIGHT_X - 1.4 * mm, y - h + 2.5 * mm, 1.4 * mm, h - 5 * mm, fill=1, stroke=0)

    c.setFillColor(NAVY_DARK)
    c.setFont(pe.FONT_NAME_BOLD, 10.5)
    c.drawRightString(RIGHT_X - 6 * mm, y - 6.6 * mm, _ar("مواد لا تتوفر لها أوقات بعد"))
    c.setFillColor(MUTED)
    c.setFont(pe.FONT_NAME, 9.5)
    ty = y - 12.4 * mm
    for line in lines:
        c.drawRightString(RIGHT_X - 6 * mm, ty, line)
        ty -= line_h
    return y - h - 4 * mm


# ---------------------------------------------------------------------------
# التذييل وترقيم الصفحات (يحتاج معرفة العدد الكلي، فيُرسم عند الحفظ)
# ---------------------------------------------------------------------------

def _draw_footer(c, page, total):
    y = 14 * mm
    c.setStrokeColor(BORDER)
    c.setLineWidth(1.0)
    c.line(LEFT_X, y, RIGHT_X, y)
    c.setFillColor(GOLD)
    c.rect(PAGE_W / 2 - 9 * mm, y - 0.5 * mm, 18 * mm, 1 * mm, fill=1, stroke=0)

    c.setFillColor(NAVY_DARK)
    c.setFont(pe.FONT_NAME_BOLD, 9)
    c.drawCentredString(PAGE_W / 2, y - 6 * mm, f"{page} / {total}")

    c.setFillColor(MUTED)
    c.setFont(pe.FONT_NAME, 8)
    c.drawRightString(RIGHT_X, y - 6 * mm, _ar("تم إنشاء هذا الجدول بواسطة بوت جدول IUST"))
    c.drawString(LEFT_X, y - 6 * mm, BRAND)


class _NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_states = []

    def showPage(self):
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_states)
        for state in self._saved_states:
            self.__dict__.update(state)
            _draw_footer(self, self._pageNumber, total)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)


def _draw_info_banner(c, y, lines):
    """صندوق ملخص (يُستخدم في الجدول المثالي لعرض عدد الأيام والفراغات)."""
    inner_w = CONTENT_W - 12 * mm
    wrapped = []
    for line in lines:
        wrapped.extend(_wrap(line, pe.FONT_NAME_BOLD, 10, inner_w))
    line_h = 5.6 * mm
    h = len(wrapped) * line_h + 6 * mm
    c.setFillColor(TINT)
    c.setStrokeColor(BORDER)
    c.setLineWidth(1.0)
    c.roundRect(LEFT_X, y - h, CONTENT_W, h, 2.5 * mm, fill=1, stroke=1)
    c.setFillColor(GOLD)
    c.rect(RIGHT_X - 1.4 * mm, y - h + 2.5 * mm, 1.4 * mm, h - 5 * mm, fill=1, stroke=0)
    c.setFillColor(NAVY_DARK)
    c.setFont(pe.FONT_NAME_BOLD, 10)
    ty = y - 6.2 * mm
    for line in wrapped:
        c.drawRightString(RIGHT_X - 6 * mm, ty, line)
        ty -= line_h
    return y - h - 7 * mm


def _make_item(s, name):
    start_min = s["start_min"]
    end_abs = sd.time_to_minutes(s["end"])
    end_min = end_abs if start_min < end_abs < 24 * 60 else start_min + 120
    item = dict(s)
    item["name"] = name
    item["end_min"] = end_min
    return item


def _render(output_path, sessions, courses_count, missing, title, subtitle, info_lines=None):
    pe._register_fonts()

    known_day_sessions = [s for s in sessions if s["day"] in sd.DAY_ORDER]
    stats = {
        "courses": courses_count,
        "sessions": len(known_day_sessions),
        "days": len({s["day"] for s in known_day_sessions}),
    }

    c = _NumberedCanvas(output_path, pagesize=A4)
    c._ws_title = title
    c._ws_subtitle = subtitle
    c.setTitle(f"{title} - WebSeeker")
    c.setAuthor("WebSeeker")

    _draw_hero(c, stats)
    y = PAGE_H - HERO_H - 18 * mm

    if info_lines:
        y = _draw_info_banner(c, y, info_lines)

    if not known_day_sessions:
        y = _draw_empty_message(c, y)
    else:
        plan = _plan_overview([s for s in known_day_sessions if s["start_min"] < 24 * 60])
        if plan:
            y = _draw_section_title(c, y, "نظرة عامة على الأسبوع", legend=True)
            y = _draw_overview(c, y, plan)
        y = _draw_section_title(c, y, "تفاصيل الحصص")
        y = _draw_days(c, y, known_day_sessions)

    if missing:
        _draw_missing_note(c, y, missing)

    c.showPage()
    c.save()


# ---------------------------------------------------------------------------
# الواجهات العامة
# ---------------------------------------------------------------------------

def build_schedule_pdf(years_data, selected_list, output_path, student_name=None):
    """ملف "عرض الجدول": المواد التي اختارها الطالب."""
    sessions, missing, courses_count = [], [], 0
    for year, code in selected_list:
        course = sd.get_course(years_data, year, code)
        if not course:
            continue
        courses_count += 1
        if not course["sessions"]:
            missing.append(course["name"])
            continue
        for s in course["sessions"]:
            sessions.append(_make_item(s, course["name"]))
    _render(output_path, sessions, courses_count, missing, TITLE, SUBTITLE)


def build_optimized_schedule_pdf(chosen_options, output_path, stats_lines=None):
    """ملف "الجدول المثالي": نفس التصميم، بعنوان مختلف وصندوق ملخص."""
    sessions = []
    course_codes = set()
    for opt in chosen_options:
        course_codes.add(opt.course_code)
        for s in opt.sessions:
            sessions.append(_make_item(s, opt.course_name))
    _render(output_path, sessions, len(course_codes), [], OPT_TITLE, OPT_SUBTITLE,
            info_lines=stats_lines)
