#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_notifier_admin.py
-----------------------
اختبارات أوامر المراجعة في شات الإشعارات (notifier_admin.py).

أهم ما تُثبته:
    1. أي شات غير شات المالك يُرفَض ولا يصل إلى البيانات إطلاقًا.
    2. الأوامر تعرض ما يجب أن تعرضه (الرقم الدائم، العدادات، الحضور).
    3. /note يحفظ فعلًا وتظهر الملاحظة afterward في /user.
    4. /export يُنتج ملفًا صالحًا.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import notifier
import notifier_admin
from test_notifier_fake_redis import FakeRedis

OWNER_CHAT_ID = "555000"


class AdminCommandTestCase(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.redis = FakeRedis()
        self.replies = []
        self.documents = []

        self._original = {
            name: getattr(notifier, name)
            for name in ("_redis_call", "_redis_pipeline", "_storage_enabled",
                         "_notify_enabled", "_send_message", "_edit_message",
                         "_delete_message", "NOTIFIER_CHAT_ID",
                         "PUSH_DEBOUNCE_SECONDS", "_presence_last_write")
        }

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
        notifier.NOTIFIER_CHAT_ID = OWNER_CHAT_ID
        notifier._send_message = lambda text: 1
        notifier._edit_message = lambda message_id, text: True
        notifier._delete_message = lambda message_id: None
        notifier.PUSH_DEBOUNCE_SECONDS = 0.01
        notifier._presence_last_write.clear()

    def tearDown(self):
        for timer in list(notifier._push_timers.values()):
            timer.cancel()
        for name, value in self._original.items():
            setattr(notifier, name, value)

    async def run_command(self, handler, args=(), chat_id=OWNER_CHAT_ID):
        """ينفّذ أمرًا مع update/context وهميين ويعيد النصوص المُرسَلة."""
        self.replies = []
        self.documents = []

        message = SimpleNamespace(
            reply_text=AsyncMock(side_effect=lambda text, **kw: self.replies.append(text)),
            reply_document=AsyncMock(side_effect=self._capture_document),
        )
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=chat_id),
            effective_message=message,
            effective_user=SimpleNamespace(id=int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0),
        )
        context = SimpleNamespace(args=list(args))
        await handler(update, context)
        return self.replies

    def _capture_document(self, **kwargs):
        payload = kwargs["document"]
        self.documents.append({
            "filename": kwargs.get("filename"),
            "bytes": payload.read(),
        })

    def seed(self, user_id=101, username="alice", full_name="Alice A", **events):
        notifier.touch(user_id, username, full_name)
        for event_type, count in events.items():
            for _ in range(count):
                notifier.log_event(user_id, username, event_type, "", full_name)
        return notifier.load_user_record(user_id)


class TestAccessControl(AdminCommandTestCase):

    async def test_foreign_chat_gets_nothing(self):
        """بيانات المستخدمين لا تُعرض إلا على شات المالك."""
        self.seed()
        for handler in (notifier_admin.cmd_stats, notifier_admin.cmd_users,
                        notifier_admin.cmd_export, notifier_admin.cmd_help):
            replies = await self.run_command(handler, chat_id="999888")
            self.assertEqual(replies, [])
            self.assertEqual(self.documents, [])

    async def test_user_lookup_is_blocked_for_foreign_chat(self):
        self.seed()
        replies = await self.run_command(notifier_admin.cmd_user, args=["101"],
                                         chat_id="999888")
        self.assertEqual(replies, [])

    async def test_note_is_blocked_for_foreign_chat(self):
        self.seed()
        await self.run_command(notifier_admin.cmd_note, args=["101", "ملاحظة"],
                               chat_id="999888")
        self.assertEqual(notifier.load_user_record(101).get("note"), "")

    def test_admin_bot_refuses_to_share_the_main_bot_token(self):
        """مستمعان لبوت واحد يعنيان تحديثات ضائعة، فيجب الرفض صراحة."""
        with patch.object(notifier, "NOTIFIER_BOT_TOKEN", "SAME-TOKEN"):
            self.assertIsNone(notifier_admin.start_in_background("SAME-TOKEN"))

    def test_admin_bot_disabled_without_token(self):
        with patch.object(notifier, "NOTIFIER_BOT_TOKEN", ""):
            self.assertIsNone(notifier_admin.start_in_background("main-token"))


class TestReadCommands(AdminCommandTestCase):

    async def test_help_lists_every_command(self):
        replies = await self.run_command(notifier_admin.cmd_help)
        text = "\n".join(replies)
        for command in ("/stats", "/users", "/user", "/note", "/export", "/reindex"):
            self.assertIn(command, text)

    async def test_stats_reports_global_numbers(self):
        self.seed(101, "alice", "Alice A", show_schedule=2, optimize_schedule=1)
        self.seed(102, "bob", "Bob B", send_all_times_pdf=1)

        text = "\n".join(await self.run_command(notifier_admin.cmd_stats))
        self.assertIn("إجمالي المستخدمين: 2", text)
        self.assertIn("جدول مواد مختارة: 2", text)
        self.assertIn("جدول مثالي: 1", text)
        self.assertIn("جدول كل المواد: 1", text)

    async def test_users_shows_number_and_online_marker(self):
        self.seed(101, "alice", "Alice A")
        self.seed(102, "bob", "Bob B")

        text = "\n".join(await self.run_command(notifier_admin.cmd_users))
        self.assertIn("#1", text)
        self.assertIn("#2", text)
        self.assertIn("@alice", text)
        self.assertIn("🟢", text)

    async def test_users_accepts_a_limit(self):
        for uid in range(201, 206):
            self.seed(uid, f"user{uid}")
        text = "\n".join(await self.run_command(notifier_admin.cmd_users, args=["2"]))
        self.assertIn("المستخدمون الأحدث انضمامًا (2)", text)

    async def test_user_card_by_permanent_number(self):
        self.seed(101, "alice", "Alice A", show_schedule=3)
        number = notifier.load_user_record(101)["user_number"]

        text = "\n".join(await self.run_command(notifier_admin.cmd_user, args=[str(number)]))
        self.assertIn(f"#{number}", text)
        self.assertIn("جدول مواد مختارة: 3", text)
        self.assertIn("(101)", text)

    async def test_user_card_lists_recent_events(self):
        """السجل التفصيلي لا يظهر في رسالة المستخدم، لكنه متاح للمالك هنا."""
        self.seed(101, "alice", "Alice A", select_subject=3)
        text = "\n".join(await self.run_command(notifier_admin.cmd_user, args=["101"]))
        self.assertIn("آخر الأحداث", text)
        self.assertIn("اختيار مادة", text)

    async def test_unknown_user_is_reported(self):
        replies = await self.run_command(notifier_admin.cmd_user, args=["777777"])
        self.assertIn("لم يُعثر", "\n".join(replies))

    async def test_user_without_arguments_shows_usage(self):
        replies = await self.run_command(notifier_admin.cmd_user)
        self.assertIn("الاستخدام", "\n".join(replies))


class TestNoteCommand(AdminCommandTestCase):
    """/note هي أداة "المراجعة والإكمال لاحقًا"."""

    async def test_note_is_persisted(self):
        self.seed(101, "alice")
        await self.run_command(notifier_admin.cmd_note, args=["101", "طالب", "سنة", "ثالثة"])
        self.assertEqual(notifier.load_user_record(101)["note"], "طالب سنة ثالثة")

    async def test_note_shows_up_in_user_card(self):
        self.seed(101, "alice")
        await self.run_command(notifier_admin.cmd_note, args=["101", "يحتاج متابعة"])
        text = "\n".join(await self.run_command(notifier_admin.cmd_user, args=["101"]))
        self.assertIn("يحتاج متابعة", text)

    async def test_note_can_be_addressed_by_permanent_number(self):
        self.seed(101, "alice")
        number = notifier.load_user_record(101)["user_number"]
        await self.run_command(notifier_admin.cmd_note, args=[str(number), "عبر الرقم"])
        self.assertEqual(notifier.load_user_record(101)["note"], "عبر الرقم")

    async def test_note_is_cleared_with_a_dash(self):
        self.seed(101, "alice")
        await self.run_command(notifier_admin.cmd_note, args=["101", "ملاحظة مؤقتة"])
        replies = await self.run_command(notifier_admin.cmd_note, args=["101", "-"])

        self.assertEqual(notifier.load_user_record(101)["note"], "")
        self.assertIn("حُذفت", "\n".join(replies))

    async def test_note_requires_text(self):
        self.seed(101, "alice")
        replies = await self.run_command(notifier_admin.cmd_note, args=["101"])
        self.assertIn("الاستخدام", "\n".join(replies))


class TestExportCommand(AdminCommandTestCase):

    async def test_csv_export_is_sent_as_a_document(self):
        self.seed(101, "alice", "Alice A", show_schedule=1)
        await self.run_command(notifier_admin.cmd_export, args=["csv"])

        self.assertEqual(len(self.documents), 1)
        document = self.documents[0]
        self.assertTrue(document["filename"].endswith(".csv"))
        self.assertTrue(document["bytes"].startswith(b"\xef\xbb\xbf"))
        self.assertIn("101", document["bytes"].decode("utf-8-sig"))

    async def test_json_export_is_valid_json(self):
        import json

        self.seed(101, "alice", "Alice A")
        await self.run_command(notifier_admin.cmd_export, args=["json"])

        payload = json.loads(self.documents[0]["bytes"].decode("utf-8"))
        self.assertEqual(payload["users"][0]["telegram_id"], 101)

    async def test_export_defaults_to_csv_and_rejects_other_formats(self):
        self.seed(101, "alice")
        await self.run_command(notifier_admin.cmd_export)
        self.assertTrue(self.documents[0]["filename"].endswith(".csv"))

        replies = await self.run_command(notifier_admin.cmd_export, args=["xml"])
        self.assertIn("الاستخدام", "\n".join(replies))


class TestReindexCommand(AdminCommandTestCase):

    async def test_reindex_reports_record_count(self):
        self.seed(101, "alice")
        self.seed(102, "bob")
        replies = await self.run_command(notifier_admin.cmd_reindex)
        self.assertIn("السجلات المحفوظة (2)", "\n".join(replies))


if __name__ == "__main__":
    unittest.main(verbosity=2)
