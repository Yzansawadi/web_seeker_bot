#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arabic_text.py
----------------
أداة مستقلة (بدون أي مكتبات خارجية) لتجهيز النص العربي للرسم الصحيح في
PDF عبر reportlab:

  1. reshape(text)  -> يستبدل كل حرف عربي بشكله الصحيح (منفصل/بداية/وسط/نهاية)
                       حسب موقعه داخل الكلمة، ويدمج اللاصقات (ligatures) الأكثر شيوعًا
                       مثل لام-ألف.
  2. to_visual(text) -> يعيد ترتيب النص بصريًا من اليمين لليسار (RTL) مع الحفاظ
                       على ترتيب المقاطع اللاتينية/الأرقام كما هي (لأنها تُكتب
                       من اليسار لليمين حتى داخل سياق عربي).

لا تعتمد هذه الوحدة على arabic_reshaper أو python-bidi، لتجنّب أي مشاكل
تثبيت على جهاز المستخدم (خصوصًا على ويندوز). تغطي معظم النصوص العادية
(عناوين المواد، أسماء المدرّسين، أيام الأسبوع) التي يحتاجها هذا البوت.
"""

import re

# ---------------------------------------------------------------------------
# جدول أشكال الحروف العربية: لكل حرف -> (منفصل، بداية، وسط، نهاية)
# المصدر: نطاق Arabic Presentation Forms-B في يونيكود (FE70-FEFF) ونطاق
# Arabic Presentation Forms-A (FB50-FDFF) للاصقات الخاصة.
# ---------------------------------------------------------------------------

# الحروف التي تتصل بما بعدها (لها أشكال بداية/وسط) والحروف التي لا تتصل
# (تُكتب فقط بشكل منفصل أو نهائي، مثل الألف والدال والراء والواو...).
FORMS = {
    'ء': ('\uFE80', '\uFE80', '\uFE80', '\uFE80'),
    'آ': ('\uFE81', '\uFE81', '\uFE81', '\uFE82'),
    'أ': ('\uFE83', '\uFE83', '\uFE83', '\uFE84'),
    'ؤ': ('\uFE85', '\uFE85', '\uFE85', '\uFE86'),
    'إ': ('\uFE87', '\uFE87', '\uFE87', '\uFE88'),
    'ئ': ('\uFE89', '\uFE8B', '\uFE8C', '\uFE8A'),
    'ا': ('\u0627', '\u0627', '\u0627', '\uFE8E'),
    'ب': ('\uFE8F', '\uFE91', '\uFE92', '\uFE90'),
    'ة': ('\uFE93', '\uFE93', '\uFE93', '\uFE94'),
    'ت': ('\uFE95', '\uFE97', '\uFE98', '\uFE96'),
    'ث': ('\uFE99', '\uFE9B', '\uFE9C', '\uFE9A'),
    'ج': ('\uFE9D', '\uFE9F', '\uFEA0', '\uFE9E'),
    'ح': ('\uFEA1', '\uFEA3', '\uFEA4', '\uFEA2'),
    'خ': ('\uFEA5', '\uFEA7', '\uFEA8', '\uFEA6'),
    'د': ('\uFEA9', '\uFEA9', '\uFEA9', '\uFEAA'),
    'ذ': ('\uFEAB', '\uFEAB', '\uFEAB', '\uFEAC'),
    'ر': ('\uFEAD', '\uFEAD', '\uFEAD', '\uFEAE'),
    'ز': ('\uFEAF', '\uFEAF', '\uFEAF', '\uFEB0'),
    'س': ('\uFEB1', '\uFEB3', '\uFEB4', '\uFEB2'),
    'ش': ('\uFEB5', '\uFEB7', '\uFEB8', '\uFEB6'),
    'ص': ('\uFEB9', '\uFEBB', '\uFEBC', '\uFEBA'),
    'ض': ('\uFEBD', '\uFEBF', '\uFEC0', '\uFEBE'),
    'ط': ('\uFEC1', '\uFEC3', '\uFEC4', '\uFEC2'),
    'ظ': ('\uFEC5', '\uFEC7', '\uFEC8', '\uFEC6'),
    'ع': ('\uFEC9', '\uFECB', '\uFECC', '\uFECA'),
    'غ': ('\uFECD', '\uFECF', '\uFED0', '\uFECE'),
    'ف': ('\uFED1', '\uFED3', '\uFED4', '\uFED2'),
    'ق': ('\uFED5', '\uFED7', '\uFED8', '\uFED6'),
    'ك': ('\uFED9', '\uFEDB', '\uFEDC', '\uFEDA'),
    'ل': ('\uFEDD', '\uFEDF', '\uFEE0', '\uFEDE'),
    'م': ('\uFEE1', '\uFEE3', '\uFEE4', '\uFEE2'),
    'ن': ('\uFEE5', '\uFEE7', '\uFEE8', '\uFEE6'),
    'ه': ('\uFEE9', '\uFEEB', '\uFEEC', '\uFEEA'),
    'و': ('\uFEED', '\uFEED', '\uFEED', '\uFEEE'),
    'ى': ('\uFEEF', '\uFEEF', '\uFEEF', '\uFEF0'),
    'ي': ('\uFEF1', '\uFEF3', '\uFEF4', '\uFEF2'),
    'لا': None,  # تُعالج كلاصقة خاصة أدناه
}

# الحروف التي لا تتصل بالحرف الذي يليها (تقطع الاتصال بعدها) حتى لو كانت
# هي نفسها متصلة بما قبلها. كل الحروف العربية تتصل بما قبلها إلا هذه
# المجموعة تحديدًا تمنع الاتصال بما بعدها.
NON_CONNECTING_AFTER = set('ادذرزوةىءأإآؤئ')

# لاصقة لام-ألف الشائعة (لا + ألف -> ﻻ) بأشكالها المنفصلة والنهائية
LAM_ALEF = {
    'ا': ('\uFEFB', '\uFEFC'),   # لا : (منعزلة/بداية كلمة جديدة, نهاية)
    'أ': ('\uFEF7', '\uFEF8'),   # لأ
    'إ': ('\uFEF9', '\uFEFA'),   # لإ
    'آ': ('\uFEF5', '\uFEF6'),   # لآ
}

ARABIC_CHAR_RE = re.compile('[\u0600-\u06FF]')
ARABIC_LETTER_SET = set(FORMS.keys()) | set(LAM_ALEF.keys()) | {'ل'}


def _is_arabic_letter(ch):
    return ch in ARABIC_LETTER_SET


def reshape(text):
    """
    يحوّل كل حرف عربي إلى شكله الصحيح (منفصل/بداية/وسط/نهاية) حسب موقعه،
    مع دمج لاصقة لام-ألف. الأحرف غير العربية (أرقام، إنجليزية، رموز) تبقى
    كما هي دون أي تغيير.
    """
    chars = list(text)
    n = len(chars)
    out = []
    i = 0

    while i < n:
        ch = chars[i]

        if not _is_arabic_letter(ch):
            out.append(ch)
            i += 1
            continue

        prev_ch = chars[i - 1] if i > 0 else None
        next_ch = chars[i + 1] if i + 1 < n else None

        # معالجة لاصقة لام + ألف (بكل أشكال الألف)
        if ch == 'ل' and next_ch in LAM_ALEF:
            connects_before = bool(prev_ch and _is_arabic_letter(prev_ch) and prev_ch not in NON_CONNECTING_AFTER)
            isolated_or_final = LAM_ALEF[next_ch]
            # إذا كان متصلًا بما قبله، لا يوجد شكل "بداية" مخصص للاصقة، فنستخدم
            # شكل النهاية نفسه (مظهره يبقى صحيحًا بصريًا في كل الحالات الشائعة)
            out.append(isolated_or_final[1] if connects_before else isolated_or_final[0])
            i += 2
            continue

        forms = FORMS.get(ch)
        if forms is None:
            out.append(ch)
            i += 1
            continue

        isolated, initial, medial, final = forms

        connects_before = bool(
            prev_ch and _is_arabic_letter(prev_ch) and prev_ch not in NON_CONNECTING_AFTER
        )
        connects_after = bool(next_ch and _is_arabic_letter(next_ch) and ch not in NON_CONNECTING_AFTER)

        if connects_before and connects_after:
            out.append(medial)
        elif connects_before and not connects_after:
            out.append(final)
        elif not connects_before and connects_after:
            out.append(initial)
        else:
            out.append(isolated)

        i += 1

    return ''.join(out)


# ---------------------------------------------------------------------------
# اتجاه الكتابة (تبسيط عملي لخوارزمية bidi لحالتنا: نصوص قصيرة مفردة السطر،
# قد تحتوي على أرقام/إنجليزية مدمجة مثل أوقات الحصص "8:00 AM - 10:00 AM").
# ---------------------------------------------------------------------------

# نطاقات الأحرف "القوية من اليسار لليمين" (لاتينية، أرقام) التي يجب أن تبقى
# بترتيبها الداخلي الطبيعي حتى عند عكس بقية النص. لا نضمّن ":" هنا عمدًا:
# في نصوص مثل "القاعة: 7004" يجب أن تبقى النقطتان ملاصقتين للكلمة العربية
# التي تسبقها (وتُعكس معها)، بينما الرقم بعدها يبقى كتلة مستقلة بترتيبه.
_LTR_RUN_RE = re.compile(r'[A-Za-z0-9./%+\-]+(?:\s[A-Za-z0-9./%+\-]+)*')


# الرموز "المرآتية" (mirrored characters): عند عرضها ضمن سياق معكوس بصريًا
# (RTL)، يجب استبدالها بمقابلها المعكوس وإلا ستبدو الأقواس مقلوبة المعنى.
_MIRROR_PAIRS = {
    '(': ')', ')': '(',
    '[': ']', ']': '[',
    '{': '}', '}': '{',
    '<': '>', '>': '<',
    '«': '»', '»': '«',
}


def _mirror(ch):
    return _MIRROR_PAIRS.get(ch, ch)


def to_visual(text):
    """
    يعيد ترتيب النص بصريًا للعرض من اليمين لليسار: يعكس ترتيب الكلمات/الحروف
    العربية عامةً، بينما يحافظ على أي مقطع لاتيني/رقمي متصل (كأوقات أو رموز)
    بترتيبه الطبيعي من اليسار لليمين داخل موضعه الجديد.

    هذا تبسيط عملي (وليس تطبيقًا كاملًا لخوارزمية Unicode Bidirectional)
    لكنه يعطي نتيجة صحيحة بصريًا لكل النصوص التي يستخدمها هذا البوت:
    عناوين عربية خالصة، أو عربية مع وقت/رمز لاتيني مدمج.
    """
    if not text:
        return text

    # نقسّم النص إلى مقاطع: كل مقطع لاتيني/رقمي متصل يبقى كما هو ككتلة واحدة،
    # وما بينها (عربي ومسافات وعلامات) يُعكس حرفًا حرفًا.
    tokens = []
    last_end = 0
    for m in _LTR_RUN_RE.finditer(text):
        if m.start() > last_end:
            tokens.append(('ar', text[last_end:m.start()]))
        tokens.append(('ltr', m.group()))
        last_end = m.end()
    if last_end < len(text):
        tokens.append(('ar', text[last_end:]))

    # نعكس ترتيب المقاطع نفسها (لأن النص يُقرأ من اليمين لليسار ككل)
    tokens.reverse()

    out_parts = []
    for kind, chunk in tokens:
        if kind == 'ltr':
            out_parts.append(chunk)
        else:
            out_parts.append(''.join(_mirror(c) for c in reversed(chunk)))

    return ''.join(out_parts)


def prepare(text):
    """دالة مساعدة واحدة: تشكيل ثم ترتيب بصري، جاهزة للرسم المباشر في reportlab."""
    return to_visual(reshape(text))
