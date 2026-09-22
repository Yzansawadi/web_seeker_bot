#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_notifier.py
-----------------
اختبارات لنظام المتابعة الجديد (notifier.py) مقابل Redis وهمي في الذاكرة.

الهدف ليس محاكاة Upstash بدقّة، بل تثبيت السلوكيات التي بُني النظام من أجلها:
    1. الرقم الدائم لا يتغير أبدًا، حتى بعد "إعادة تشغيل" البوت.
    2. إعادة التشغيل لم تعد تمحو سجلات المستخدمين (العلّة القديمة).
    3. العدادات الثلاثة منفصلة وصحيحة.
    4. رسالة واحدة لكل مستخدم مهما تتابعت الأحداث (debounce).
    5. ترحيل السجلات القديمة لا يفقد التاريخ ولا يتركها بلا رقم.
    6. الحضور، اللوحة، والتصدير تعمل على الفهارس لا على مسح المفاتيح.

التشغيل:
    python test_notifier.py
"""

import json
import time
import unittest

import notifier
from test_notifier_fake_redis import FakeRedis


class NotifierTestCase(unittest.TestCase):
    """يُهيّئ Redis وهميًا ويعطّل إرسال تيليغرام الحقيقي في كل اختبار."""

    def setUp(self):
        self.redis = FakeRedis()
        self.sent_messages = []
        self.edited_messages = []

        self._original = {
            name: getattr(notifier, name)
            for name in (
                "_redis_call", "_redis_pipeline", "_storage_enabled",
                "_notify_enabled", "_send_message", "_edit_message",
                "_delete_message", "PUSH_DEBOUNCE_SECONDS", "_push_timers",
                "_pending_records", "_presence_last_write",
            )
        }

        # البديلان يحترمان `_storage_enabled` تمامًا كما يفعل التنفيذ الحقيقي؛
        # بدونهما يصبح اختبار "التخزين معطَّل" بلا معنى.
        notifier._redis_call = (
            lambda *parts: self.redis.execute(list(parts))
            if notifier._storage_enabled else None
        )
        notifier._redis_pipeline = (
            lambda cmds: [self.redis.execute(list(c)) for c in cmds]
            if notifier._storage_enabled else None
        )
        notifier._storage_enabled = True
        notifier._notify_enabled = True
        notifier._send_message = self._fake_send
        notifier._edit_message = self._fake_edit
        notifier._delete_message = lambda message_id: None
        # مهلة قصيرة جدًا لنضطر لانتظار ثوانٍ في كل اختبار.
        notifier.PUSH_DEBOUNCE_SECONDS = 0.05

        notifier._push_timers.clear()
        notifier._pending_records.clear()
        notifier._presence_last_write.clear()

    def tearDown(self):
        for timer in list(notifier._push_timers.values()):
            timer.cancel()
        for name, value in self._original.items():
            setattr(notifier, name, value)

    def _fake_send(self, text):
        self.sent_messages.append(text)
        return len(self.sent_messages)

    def _fake_edit(self, message_id, text):
        self.edited_messages.append((message_id, text))
        return True

    def flush_pending_pushes(self, wait=0.3):
        """ينتظر حتى تنطلق كل الدفعات المؤجَّلة المجدولة."""
        time.sleep(wait)

    def record_of(self, user_id):
        return notifier.load_user_record(user_id)


class TestPermanentNumber(NotifierTestCase):
    """الطلب 1: ترقيم خاص بالمستخدم لا يتغير أبدًا."""

    def test_numbers_are_sequential_and_unique(self):
        notifier.touch(101, "alice", "Alice A")
        notifier.touch(102, "bob", "Bob B")

        self.assertEqual(self.record_of(101)["user_number"], 1)
        self.assertEqual(self.record_of(102)["user_number"], 2)

    def test_number_survives_simulated_restart(self):
        """العلّة القديمة: `seen_users` في الذاكرة كانت تُفرَّغ مع كل إعادة
        تشغيل، فيُعامل كل مستخدم قديم كجديد ويُمحى سجله. هنا نفرّغ كل حالة
        الذاكرة (محاكاة إعادة تشغيل) ونتأكد أن الرقم والعدادات باقية."""
        notifier.touch(101, "alice", "Alice A")
        notifier.log_event(101, "alice", "show_schedule")
        notifier.log_event(101, "alice", "show_schedule")
        before = self.record_of(101)

        # محاكاة إعادة تشغيل: كل حالة الذاكرة تُفرَّغ، ولا يبقى إلا Redis.
        notifier._presence_last_write.clear()
        notifier.touch(101, "alice", "Alice A")

        after = self.record_of(101)
        self.assertEqual(after["user_number"], before["user_number"])
        self.assertEqual(after["join_date"], before["join_date"])
        self.assertEqual(after["actions"]["show_schedule"], 2)

    def test_number_is_not_reused_after_record_churn(self):
        notifier.touch(101, "alice")
        notifier.touch(102, "bob")
        notifier._presence_last_write.clear()
        notifier.touch(101, "alice")

        self.assertEqual(self.record_of(101)["user_number"], 1)
        self.assertEqual(self.redis.get_int(notifier.TOTAL_USERS_COUNTER_KEY), 2)


class TestThreeCounters(NotifierTestCase):
    """الطلب 3: عدّادات منفصلة للأنواع الثلاثة."""

    def test_counters_are_independent(self):
        for _ in range(2):
            notifier.log_event(101, "alice", "show_schedule")
        notifier.log_event(101, "alice", "send_all_times_pdf")
        for _ in range(3):
            notifier.log_event(101, "alice", "optimize_schedule")

        actions = self.record_of(101)["actions"]
        self.assertEqual(actions["show_schedule"], 2)
        self.assertEqual(actions["send_all_times_pdf"], 1)
        self.assertEqual(actions["optimize_schedule"], 3)
        self.assertEqual(self.record_of(101)["total_actions"], 6)

    def test_all_times_pdf_is_now_tracked(self):
        """كان send_all_times_pdf غير مُتابَع إطلاقًا في bot.py — هذه هي
        الحالة التي أُضيفت، ونتأكد أن لها عدّادًا وظاهرًا في الرسالة."""
        notifier.log_event(101, "alice", "send_all_times_pdf")
        message = notifier._build_user_message(self.record_of(101))

        self.assertIn("جدول كل المواد: 1", message)
        self.assertIn("جدول مواد مختارة: 0", message)
        self.assertIn("جدول مثالي: 0", message)

    def test_global_action_totals(self):
        notifier.log_event(101, "alice", "show_schedule")
        notifier.log_event(102, "bob", "show_schedule")
        notifier.log_event(102, "bob", "optimize_schedule")

        stats = notifier.get_stats()
        self.assertEqual(stats["action_totals"]["show_schedule"], 2)
        self.assertEqual(stats["action_totals"]["optimize_schedule"], 1)
        self.assertEqual(stats["total_actions"], 3)


class TestUserMessage(NotifierTestCase):
    """الطلبان 2 و4: الهوية والحالة في رسالة واحدة."""

    def test_message_contains_identity_and_number(self):
        notifier.touch(101, "alice", "Alice A")
        message = notifier._build_user_message(self.record_of(101))

        self.assertIn("#1", message)
        self.assertIn("@alice", message)
        self.assertIn("Alice A", message)
        self.assertIn("id: 101", message)

    def test_online_indicator_reflects_recent_activity(self):
        notifier.touch(101, "alice")
        self.assertIn("🟢", notifier._build_user_message(self.record_of(101)))

        record = self.record_of(101)
        record["last_active_epoch"] -= notifier.ONLINE_WINDOW_SECONDS + 60
        self.assertIn("⚪", notifier._build_user_message(record))

    def test_events_list_is_not_dumped_into_message(self):
        """قرار المستخدم: العدادات فقط في الرسالة، والسجل يبقى للتخزين."""
        for _ in range(5):
            notifier.log_event(101, "alice", "select_subject", "رياضيات")
        self.flush_pending_pushes()

        message = self.sent_messages[-1]
        self.assertNotIn("event 1", message)
        self.assertNotIn("select_subject", message)
        self.assertEqual(len(self.record_of(101)["events"]), 5)

    def test_note_appears_in_message(self):
        notifier.touch(101, "alice")
        notifier.set_note(101, "طالب سنة ثالثة، يحتاج متابعة")
        self.assertIn("طالب سنة ثالثة", notifier._build_user_message(self.record_of(101)))


class TestSingleMessagePerUser(NotifierTestCase):
    """الطلب 5: رسالة واحدة تتجدد ولا تتكرر."""

    def test_rapid_events_produce_one_message(self):
        for i in range(6):
            notifier.log_event(101, "alice", "select_subject", f"مادة {i}")
        self.flush_pending_pushes()

        self.assertEqual(len(self.sent_messages), 1)
        self.assertIn("مواد اختارها: 6", self.sent_messages[0])

    def test_message_id_is_tracked_for_replacement(self):
        notifier.log_event(101, "alice", "select_subject", "رياضيات")
        self.flush_pending_pushes()
        first_id = self.record_of(101)["message_id"]
        self.assertIsNotNone(first_id)

        notifier.log_event(101, "alice", "show_schedule")
        self.flush_pending_pushes()
        second_id = self.record_of(101)["message_id"]

        self.assertEqual(len(self.sent_messages), 2)
        self.assertNotEqual(first_id, second_id)

    def test_separate_users_have_separate_messages(self):
        notifier.log_event(101, "alice", "show_schedule")
        notifier.log_event(102, "bob", "show_schedule")
        self.flush_pending_pushes()

        self.assertEqual(len(self.sent_messages), 2)
        self.assertNotEqual(self.record_of(101)["message_id"],
                            self.record_of(102)["message_id"])


class TestMigration(NotifierTestCase):
    """السجلات القديمة تُكمَل ولا تُرمى."""

    def _write_legacy_record(self, user_id):
        legacy = {
            "telegram_id": user_id,
            "username": "olduser",
            "join_date": "2026-06-25",
            "last_active_iso": "2026-06-25T10:00:00+03:00",
            "schedules_created": 4,
            "events": [
                {"type": "select_subject", "value": "فيزياء"},
                {"type": "select_subject", "value": "كيمياء"},
                {"type": "back_button", "value": ""},
            ],
            "message_id": 77,
        }
        notifier.save_user_record(user_id, legacy)

    def test_legacy_record_gains_permanent_number(self):
        self._write_legacy_record(201)
        notifier.touch(201, "olduser")

        record = self.record_of(201)
        self.assertTrue(record["user_number"])
        self.assertEqual(record["schema_version"], notifier.SCHEMA_VERSION)

    def test_legacy_counters_are_preserved(self):
        """`schedules_created` القديم كان يزيد فقط عند show_schedule، فيُرحَّل
        إلى عدّاده الجديد دون أن يضيع، وبقية الأحداث تُحتسب من السجل."""
        self._write_legacy_record(201)
        notifier.touch(201, "olduser")

        actions = self.record_of(201)["actions"]
        self.assertEqual(actions["show_schedule"], 4)
        self.assertEqual(actions["select_subject"], 2)
        self.assertEqual(actions["back_button"], 1)
        self.assertNotIn("schedules_created", self.record_of(201))

    def test_legacy_join_date_is_kept(self):
        self._write_legacy_record(201)
        notifier.touch(201, "olduser")
        self.assertEqual(self.record_of(201)["join_date"], "2026-06-25")


class TestPresenceAndDashboard(NotifierTestCase):
    """الطلب 4 والإحصائيات."""

    def test_online_users_are_listed(self):
        notifier.touch(101, "alice")
        notifier.touch(102, "bob")
        self.assertEqual(set(notifier._online_user_ids()), {101, 102})

    def test_expired_presence_is_pruned(self):
        notifier.touch(101, "alice")
        notifier._redis_call("ZADD", notifier.PRESENCE_KEY,
                             time.time() - notifier.ONLINE_WINDOW_SECONDS - 10, "999")
        self.assertNotIn(999, notifier._online_user_ids())

    def test_dashboard_shows_requested_statistics(self):
        notifier.touch(101, "alice", "Alice A")
        notifier.touch(102, "bob", "Bob B")
        for _ in range(3):
            notifier.log_event(101, "alice", "show_schedule")
        notifier.log_event(102, "bob", "optimize_schedule")

        text = notifier.build_dashboard_text(notifier.get_stats())
        self.assertIn("إجمالي المستخدمين: 2", text)
        self.assertIn("جدد اليوم: 2", text)
        self.assertIn("جدول مواد مختارة: 3", text)
        self.assertIn("جدول مثالي: 1", text)
        self.assertIn("أنشط 5 مستخدمين", text)
        self.assertIn("ذروة الاستخدام", text)

    def test_dashboard_refresh_creates_then_edits(self):
        notifier.touch(101, "alice")
        notifier.refresh_dashboard()
        dashboard_id = self.redis.strings[notifier.DASHBOARD_MESSAGE_KEY]
        self.assertTrue(dashboard_id)
        sent_after_first = len(self.sent_messages)

        # لا شيء تغيّر -> لا يُرسَل أي طلب جديد إلى تيليغرام.
        notifier.refresh_dashboard()
        self.assertEqual(len(self.sent_messages), sent_after_first)
        self.assertEqual(self.edited_messages, [])

        notifier.log_event(101, "alice", "optimize_schedule")
        self.flush_pending_pushes()
        notifier.refresh_dashboard()
        # اللوحة تُعدَّل في مكانها ولا تُستبدَل برسالة جديدة.
        self.assertEqual(self.redis.strings[notifier.DASHBOARD_MESSAGE_KEY], dashboard_id)
        self.assertEqual(len(self.edited_messages), 1)


class TestAdminDataAccess(NotifierTestCase):
    """الطلب 7: مكان للمراجعة والإكمال لاحقًا."""

    def test_find_by_id_number_and_username(self):
        notifier.touch(101, "alice", "Alice A")
        number = self.record_of(101)["user_number"]

        self.assertEqual(notifier.find_user("101")["telegram_id"], 101)
        self.assertEqual(notifier.find_user(str(number))["telegram_id"], 101)
        self.assertEqual(notifier.find_user("@alice")["telegram_id"], 101)
        self.assertIsNone(notifier.find_user("777777"))

    def test_recent_users_are_newest_first(self):
        notifier.touch(101, "alice")
        notifier.touch(102, "bob")
        recent = notifier.list_recent_users(10)
        self.assertEqual([r["telegram_id"] for r in recent], [102, 101])

    def test_csv_export_is_utf8_bom_and_parses(self):
        import csv
        import io

        notifier.touch(101, "alice", "Alice A")
        notifier.log_event(101, "alice", "show_schedule")
        notifier.set_note(101, "مراجعة لاحقة")

        raw = notifier.export_csv()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "يفتقد BOM فيتعذّر فتح العربية في Excel")

        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        self.assertEqual(rows[0][:3], ["الرقم الدائم", "المعرّف", "اسم المستخدم"])
        self.assertEqual(rows[1][1], "101")
        self.assertIn("مراجعة لاحقة", rows[1][-1])

    def test_json_export_contains_users_and_stats(self):
        notifier.touch(101, "alice")
        payload = json.loads(notifier.export_json().decode("utf-8"))

        self.assertEqual(payload["schema_version"], notifier.SCHEMA_VERSION)
        self.assertIn("stats", payload)
        self.assertEqual(payload["users"][0]["telegram_id"], 101)

    def test_rebuild_indexes_restores_stats(self):
        notifier.touch(101, "alice", "Alice A")
        notifier.log_event(101, "alice", "show_schedule")

        for key in (notifier.USERS_INDEX_KEY, notifier.ACTIVITY_INDEX_KEY,
                    notifier.ACTION_TOTALS_KEY, notifier.HOUR_HISTOGRAM_KEY):
            notifier._redis_call("DEL", key)
        self.assertEqual(notifier.get_stats()["total_actions"], 0)

        rebuilt = notifier.rebuild_indexes()
        stats = notifier.get_stats()
        self.assertEqual(rebuilt, 1)
        self.assertEqual(stats["action_totals"]["show_schedule"], 1)
        self.assertEqual(stats["total_users"], 1)

    def test_reindex_never_lowers_the_number_counter(self):
        """ضمان الرقم الدائم: تخفيض العدّاد يعني أن مستخدمًا جديدًا قد يرث رقم
        مستخدم قديم. هنا نفقد سجلًا ثم نُعيد البناء ونتأكد أن الترقيم يستمر."""
        for uid in (101, 102, 103):
            notifier.touch(uid, f"user{uid}")
        notifier._redis_call("DEL", "iust_user:103")

        before = self.redis.get_int(notifier.TOTAL_USERS_COUNTER_KEY)
        notifier.rebuild_indexes()
        after = self.redis.get_int(notifier.TOTAL_USERS_COUNTER_KEY)

        self.assertGreaterEqual(after, before)
        notifier.touch(104, "user104")
        self.assertEqual(self.record_of(104)["user_number"], before + 1)


class TestDisabledBackend(NotifierTestCase):
    """بدون Redis أو تيليغرام يجب ألا يتعطل أي شيء ولا يُرسَل أي طلب."""

    def test_everything_is_a_no_op_without_storage(self):
        notifier._storage_enabled = False
        notifier._notify_enabled = False

        notifier.touch(101, "alice", "Alice A")
        notifier.log_event(101, "alice", "show_schedule")
        notifier.refresh_dashboard()

        self.assertIsNone(notifier.load_user_record(101))
        self.assertEqual(self.sent_messages, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
