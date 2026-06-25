#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اختبار سريع (smoke test) لمنطق الحالات في bot.py، بدون الحاجة لتثبيت
python-telegram-bot أو الاتصال الفعلي بتيليغرام.
"""

import sys
import types

telegram_stub = types.ModuleType("telegram")


class _FakeButton:
    def __init__(self, label, callback_data=None):
        self.label = label
        self.callback_data = callback_data

    def __repr__(self):
        return f"[{self.label}]"


class _FakeMarkup:
    def __init__(self, rows):
        self.rows = rows


class _FakeUpdate:
    pass


telegram_stub.Update = _FakeUpdate
telegram_stub.InlineKeyboardButton = _FakeButton
telegram_stub.InlineKeyboardMarkup = _FakeMarkup

telegram_ext_stub = types.ModuleType("telegram.ext")


class _FakeApplication:
    @staticmethod
    def builder():
        raise RuntimeError("غير مستخدم في الاختبار")


class _FakeHandler:
    def __init__(self, *a, **k):
        pass


class _FakeContextTypes:
    DEFAULT_TYPE = object


telegram_ext_stub.Application = _FakeApplication
telegram_ext_stub.CommandHandler = _FakeHandler
telegram_ext_stub.CallbackQueryHandler = _FakeHandler
telegram_ext_stub.ContextTypes = _FakeContextTypes

sys.modules["telegram"] = telegram_stub
sys.modules["telegram.ext"] = telegram_ext_stub

import asyncio  # noqa: E402
import schedule_data as sd  # noqa: E402
import bot  # noqa: E402
import pdf_export  # noqa: E402


class FakeMessage:
    def __init__(self):
        self.chat_id = 12345


class FakeQuery:
    def __init__(self, data, user_id=999):
        self.data = data
        self.message = FakeMessage()
        self.from_user = types.SimpleNamespace(id=user_id)
        self.last_text = None
        self.last_markup = None

    async def answer(self, *a, **k):
        pass

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.last_text = text
        self.last_markup = reply_markup
        print("[تعديل الرسالة]")
        print(text)
        if reply_markup:
            for row in reply_markup.rows:
                print("  ", [str(b) for b in row])


class FakeBot:
    def __init__(self):
        self.sent_documents = []
        self.sent_messages = []

    async def send_document(self, chat_id, document, filename, **kwargs):
        self.sent_documents.append(filename)
        print(f"[إرسال ملف] {filename}")

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        self.sent_messages.append(text)
        print("[رسالة جديدة]")
        print(text)
        if reply_markup:
            for row in reply_markup.rows:
                print("  ", [str(b) for b in row])


class FakeContext:
    def __init__(self):
        self.bot = FakeBot()


def labels_of(markup):
    return [b.label for row in markup.rows for b in row]


async def main():
    user_id = 999
    bot.reset_session(user_id)
    years_data = sd.load_courses()
    ctx = FakeContext()

    print("=" * 70)
    print("اختبار 1 (إعادة إنتاج الخلل الأصلي): اختيار مادة، دخول قائمة")
    print("سنتها، ثم رجوع -- يجب أن تبقى المادة محفوظة")
    print("=" * 70)

    courses_year2 = sd.get_courses_for_year(years_data, 2)
    code_a = courses_year2[0]["code"]

    q1 = FakeQuery("year:2", user_id)
    await bot.show_year_courses(q1, ctx, 2)

    q2 = FakeQuery(f"course:2:{code_a}", user_id)
    await bot.select_course(q2, ctx, 2, code_a)
    session = bot.get_session(user_id)
    assert session["selected"] == [(2, code_a)], "لم يتم تسجيل أول اختيار"

    # يدخل المستخدم مجددًا لقائمة مواد السنة 2 (نفس السنة التي اختار منها)
    q3 = FakeQuery("year:2", user_id)
    await bot.show_year_courses(q3, ctx, 2)
    assert any(b.label.startswith("✓") for b in [
        btn for row in q3.last_markup.rows for btn in row
    ]), "علامة الصح لم تظهر أمام المادة المختارة سابقًا"

    # يضغط "رجوع" -- يجب أن يبقى الاختيار محفوظًا (هذا هو الخلل الذي تم إصلاحه)
    q4 = FakeQuery("back_home", user_id)
    await bot.back_home(q4, ctx)
    session = bot.get_session(user_id)
    assert session["selected"] == [(2, code_a)], (
        f"خلل: تم نسيان الاختيار بعد الرجوع! المحتوى الحالي: {session['selected']}"
    )
    assert "عرض الجدول" in labels_of(q4.last_markup), "زر عرض الجدول يجب أن يظهر لأن هناك اختيارًا محفوظًا"
    print("\nنجح: الرجوع لم يصفّر الاختيار المحفوظ.\n")

    print("=" * 70)
    print("اختبار 2: زر 'حذف مادة' يظهر في الشاشة الرئيسية وفي شاشة مواد السنة")
    print("=" * 70)
    assert "حذف مادة" in labels_of(q4.last_markup), "زر حذف مادة غائب عن الشاشة الرئيسية"

    q5 = FakeQuery("year:1", user_id)
    await bot.show_year_courses(q5, ctx, 1)
    assert "حذف مادة" in labels_of(q5.last_markup), "زر حذف مادة غائب عن شاشة مواد السنة"
    print("\nنجح: زر حذف مادة ظاهر في كل القوائم بعد أول اختيار.\n")

    print("=" * 70)
    print("اختبار 3: إضافة مادة ثانية، ثم استخدام 'حذف مادة' من الشاشة الرئيسية")
    print("=" * 70)
    courses_year1 = sd.get_courses_for_year(years_data, 1)
    code_b = courses_year1[0]["code"]
    q6 = FakeQuery(f"course:1:{code_b}", user_id)
    await bot.select_course(q6, ctx, 1, code_b)
    session = bot.get_session(user_id)
    assert session["selected"] == [(2, code_a), (1, code_b)], "يجب أن تحتوي الجلسة على مادتين"

    q7 = FakeQuery("delete_menu:home", user_id)
    await bot.show_delete_menu(q7, ctx, "home")
    delete_labels = labels_of(q7.last_markup)
    assert "تراجع" in delete_labels, "زر تراجع غائب من قائمة الحذف"

    q8 = FakeQuery(f"delete_course:2:{code_a}:home", user_id)
    await bot.delete_course(q8, ctx, 2, code_a, "home")
    session = bot.get_session(user_id)
    assert session["selected"] == [(1, code_b)], (
        f"يجب أن تبقى فقط المادة الثانية بعد الحذف، الموجود: {session['selected']}"
    )
    print("\nنجح: تم حذف المادة الصحيحة فقط، والمادة الأخرى بقيت محفوظة.\n")

    print("=" * 70)
    print("اختبار 4: حذف من داخل شاشة مواد سنة، يجب العودة لنفس شاشة السنة")
    print("=" * 70)
    q9 = FakeQuery(f"course:2:{code_a}", user_id)
    await bot.select_course(q9, ctx, 2, code_a)  # إعادة إضافتها للاختبار

    q10 = FakeQuery("year:1", user_id)
    await bot.show_year_courses(q10, ctx, 1)
    q11 = FakeQuery("delete_menu:year-1", user_id)
    await bot.show_delete_menu(q11, ctx, "year-1")

    q12 = FakeQuery(f"delete_course:1:{code_b}:year-1", user_id)
    await bot.delete_course(q12, ctx, 1, code_b, "year-1")
    session = bot.get_session(user_id)
    assert session["selected"] == [(2, code_a)], f"يجب أن تبقى فقط مادة السنة 2: {session['selected']}"
    assert "مواد" in q12.last_text, "يجب أن نعود لشاشة مواد السنة بعد الحذف، لا للشاشة الرئيسية"
    print("\nنجح: بعد الحذف من شاشة سنة، عاد المستخدم لنفس شاشة مواد السنة.\n")

    print("=" * 70)
    print("اختبار 5: 'عرض الجدول' لا يزال يصفّر كل شيء بعد الإرسال (لم ينكسر)")
    print("=" * 70)
    q13 = FakeQuery("show_schedule", user_id)
    await bot.show_schedule(q13, ctx)
    session = bot.get_session(user_id)
    assert session["selected"] == [], "يجب أن تُصفَّر الاختيارات بعد عرض الجدول"
    assert len(ctx.bot.sent_documents) == 1, "يجب إرسال ملف PDF واحد"
    print("\nنجح: عرض الجدول ما زال يصفّر كل شيء كما هو متوقع.\n")

    print("=" * 70)
    print("اختبار 6: قياس سرعة الكاش (يجب أن يكون التحميل الثاني أسرع بكثير)")
    print("=" * 70)
    import time
    sd._cache["years"] = None  # إجبار قراءة أولى من القرص لقياس عادل
    t0 = time.time()
    sd.load_courses()
    t1 = time.time()
    sd.load_courses()
    t2 = time.time()
    first_ms = (t1 - t0) * 1000
    second_ms = (t2 - t1) * 1000
    print(f"التحميل الأول: {first_ms:.2f} مللي ثانية | التحميل الثاني (كاش): {second_ms:.3f} مللي ثانية")
    assert second_ms < first_ms / 5, "التخزين المؤقت لا يبدو فعالًا كما هو متوقع"
    print("\nنجح: التخزين المؤقت يسرّع التحميلات اللاحقة بشكل كبير.\n")

    print("تم تنفيذ كل الاختبارات بنجاح بدون أي استثناءات أو فشل في التحققات.")


asyncio.run(main())
