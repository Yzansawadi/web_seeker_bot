#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_notifier_wiring.py
------------------------
اختبارات ربط بين bot.py ونظام المتابعة الجديد.

تركّز على سؤال واحد: **هل كل إجراء في البوت يصل إلى notifier بنوع الحدث
الصحيح؟** هذا هو المكان الذي كان فيه الخلل الأصلي — "إرسال أوقات جميع المواد"
كان يعدّل شاشة المستخدم ويرسل ملف PDF دون أن يُسجَّل أي حدث، فلم يكن له عدّاد.

لا تُشغَّل هنا المعالجات الحقيقية (لا بناء PDF ولا محرك تحسين) بل تُستبدل
ببدائل، لأن المطلوب فحص طبقة التسجيل فقط.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import notifier


class WiringTestCase(unittest.IsolatedAsyncioTestCase):
    """يمرّر ضغطة زر واحدة عبر button_handler ويلتقط الأحداث المسجّلة."""

    async def fire_button(self, data, user_id=123):
        recorded = []
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=user_id, username="alice", full_name="Alice A"),
            data=data,
            message=SimpleNamespace(chat_id=456),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query, effective_user=query.from_user)
        context = SimpleNamespace(bot=SimpleNamespace(
            send_message=AsyncMock(), send_document=AsyncMock(),
        ))

        def capture(user_id_, user_, event_type, value=""):
            recorded.append((event_type, value))

        with (
            patch.object(bot, "_log_user_event", side_effect=capture),
            patch.object(bot, "track_user"),
            patch.object(bot.sd, "load_courses", return_value={}),
            patch.object(bot.sd, "get_course", return_value={"name": "رياضيات"}),
            patch.object(bot, "run_update_schedule", AsyncMock()),
            patch.object(bot, "send_all_times_pdf", AsyncMock()),
            patch.object(bot, "show_delete_menu", AsyncMock()),
            patch.object(bot, "delete_course", AsyncMock()),
            patch.object(bot, "show_year_courses", AsyncMock()),
            patch.object(bot, "select_course", AsyncMock()),
            patch.object(bot, "show_schedule", AsyncMock()),
            patch.object(bot, "optimize_schedule", AsyncMock()),
            patch.object(bot, "go_back", AsyncMock()),
            patch.object(bot, "go_start", AsyncMock()),
        ):
            await bot.button_handler(update, context)

        return recorded


class TestThreeMainCountersAreWired(WiringTestCase):
    """العدادات الثلاثة المطلوبة يجب أن يكون لكلٍّ منها حدث فعّال."""

    async def test_selected_schedule_is_logged(self):
        self.assertEqual(await self.fire_button("show_schedule"), [("show_schedule", "")])

    async def test_all_times_pdf_is_logged(self):
        """هذا هو الإصلاح: لم يكن send_all_times_pdf يُسجَّل إطلاقًا."""
        self.assertEqual(await self.fire_button("send_all_times_pdf"),
                         [("send_all_times_pdf", "")])

    async def test_optimized_schedule_is_logged_once(self):
        """كان يُسجَّل مرتين محتملتين (في السلسلة وفي فرع المعالجة)؛ صار مرة واحدة."""
        self.assertEqual(await self.fire_button("optimize_schedule"),
                         [("optimize_schedule", "")])

    async def test_update_schedule_is_logged(self):
        self.assertEqual(await self.fire_button("update_schedule"),
                         [("update_schedule", "")])


class TestNavigationEvents(WiringTestCase):
    async def test_back_and_go_start_share_one_event(self):
        self.assertEqual(await self.fire_button("back"), [("back_button", "")])
        self.assertEqual(await self.fire_button("go_start"), [("back_button", "")])

    async def test_year_carries_its_value(self):
        self.assertEqual(await self.fire_button("year:2"), [("select_year", "2")])

    async def test_subject_carries_its_name(self):
        self.assertEqual(await self.fire_button("course:1:MATH1"),
                         [("select_subject", "رياضيات")])

    async def test_delete_subject_is_logged(self):
        self.assertEqual(await self.fire_button("delete_course:1:MATH1"),
                         [("delete_subject", "")])

    async def test_pure_navigation_is_not_counted_as_achievement(self):
        """اختيار المسار وضغطة "التحديث متاح بعد" ليست إنجازات، فلا تُحتسب."""
        self.assertEqual(await self.fire_button("mode:show"), [])
        self.assertEqual(await self.fire_button("mode:optimize"), [])
        self.assertEqual(await self.fire_button("update_cooldown"), [])


class TestUserTracking(WiringTestCase):
    """كل تفاعل يمرّ عبر نبضة حضور، و/start يُسجَّل كحدث.

    ملاحظة تقنية: `asyncio.to_thread` دالة غير متزامنة، فـ patch تستبدلها
    تلقائيًا بـ AsyncMock لا يُنفَّذ side_effect الخاص بها إلا عند الانتظار —
    ولهذا نمرّر البديل عبر `new=` لنحصل على تنفيذ فوري متزامن داخل الاختبار.
    """

    def test_track_user_sends_presence_pulse(self):
        calls = []
        user = SimpleNamespace(id=777, username="alice", full_name="Alice A")
        with patch.object(bot.notifier, "touch",
                          side_effect=lambda *a, **k: calls.append(a)):
            with patch.object(bot.asyncio, "to_thread",
                              new=lambda fn, *a, **k: fn(*a, **k)):
                with patch.object(bot.asyncio, "create_task", new=lambda c: c):
                    bot.track_user(user)

        self.assertEqual(calls, [(777, "alice", "Alice A")])

    def test_track_user_is_safe_to_call_repeatedly(self):
        """القرار "هل هو جديد؟" صار داخل notifier (من Redis)، لا في الذاكرة،
        فاستدعاء track_user مرات لا يُنتج سجلات مكررة."""
        user = SimpleNamespace(id=778, username="bob", full_name="Bob B")
        with patch.object(bot.notifier, "touch", return_value=None) as touch_mock:
            with patch.object(bot.asyncio, "to_thread",
                              new=lambda fn, *a, **k: fn(*a, **k)):
                with patch.object(bot.asyncio, "create_task", new=lambda c: c):
                    bot.track_user(user)
                    bot.track_user(user)
                    bot.track_user(user)
        self.assertEqual(touch_mock.call_count, 3)

    async def test_start_command_is_logged(self):
        recorded = []
        user = SimpleNamespace(id=777, username="alice", full_name="Alice A")
        update = SimpleNamespace(
            effective_user=user,
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

        with (
            patch.object(bot, "_log_user_event",
                         side_effect=lambda uid, u, ev, v="": recorded.append(ev)),
            patch.object(bot, "track_user"),
        ):
            await bot.start(update, context)

        self.assertEqual(recorded, ["start_command"])


class TestEventTypesAreKnownToNotifier(WiringTestCase):
    """كل نوع حدث يُرسله bot.py يجب أن يكون له تسمية عرض في notifier،
    وإلا ظهر في اللوحة كاسم تقني خام."""

    async def test_all_fired_event_types_have_labels(self):
        fired = set()
        for data in ("show_schedule", "optimize_schedule", "send_all_times_pdf",
                     "update_schedule", "back", "year:1", "course:1:X",
                     "delete_course:1:X"):
            for event_type, _ in await self.fire_button(data):
                fired.add(event_type)
        fired.add("start_command")

        missing = fired - set(notifier.ACTION_LABELS)
        self.assertEqual(missing, set(), f"أنواع أحداث بلا تسمية عرض: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
