"""Offline Telegram-handler integration tests with real PDF generation."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import bot
from test_schedule_optimizer import courses, meeting, solve


class OptimizerBotTests(unittest.IsolatedAsyncioTestCase):
    async def run_handler(self, data, result=None):
        bot.reset_session(123)
        bot.get_session(123)["selected"] = [(1, code) for code in data[1]]
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=123), message=SimpleNamespace(chat_id=456),
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        documents = []

        async def capture_document(**kwargs):
            self.assertTrue(kwargs["document"].read().startswith(b"%PDF-"))
            documents.append((kwargs["filename"], kwargs["caption"]))

        context = SimpleNamespace(bot=SimpleNamespace(
            send_document=AsyncMock(side_effect=capture_document), send_message=AsyncMock(),
        ))
        with TemporaryDirectory(prefix="optimizer-test-", dir=Path.home()) as output:
            with (
                patch.object(bot.sd, "load_courses", return_value=data),
                patch.object(bot, "TEMP_DIR", output),
                patch.object(bot.opt, "find_best_schedules", wraps=bot.opt.find_best_schedules) as search,
            ):
                if result is not None:
                    search.side_effect = lambda *args, **kwargs: result
                await bot.optimize_schedule(query, context)
                self.assertEqual(search.call_args.kwargs["top_n"], 3)
                self.assertEqual(os.listdir(output), [])
        self.assertEqual(bot.get_session(123)["selected"], [])
        return documents, context.bot.send_message

    async def test_best_and_only_close_alternative_are_sent_in_order(self):
        data = courses([
            meeting("الاثنين"), meeting("الثلاثاء", section="2"),
            meeting("الاثنين", start=490, end=550, section="3"),
        ])
        documents, messages = await self.run_handler(data)
        self.assertEqual([name for name, _ in documents], [
            "webseeker_schedule.pdf", "webseeker_alternative_1.pdf",
        ])
        self.assertEqual(documents[0][1], "الجدول الأفضل بصيغة PDF")
        self.assertIn("بديل مشابه 1", documents[1][1])
        self.assertIn("عدد البدائل المشابهة: 1", messages.call_args_list[0].kwargs["text"])

    async def test_unique_best_sends_one_pdf(self):
        documents, _ = await self.run_handler(courses([meeting("الاثنين")]))
        self.assertEqual(len(documents), 1)

    async def test_timeout_without_incumbent_sends_no_pdf(self):
        data = courses([meeting("الاثنين")])
        result = solve(data, time_budget_seconds=0)
        documents, messages = await self.run_handler(data, result)
        self.assertEqual(documents, [])
        self.assertIn("لم يثبت", messages.call_args_list[0].kwargs["text"])

    async def test_unproven_result_is_labeled_in_summary_and_pdf_caption(self):
        data = courses([meeting("الاثنين")])
        result = solve(data)
        result.update(timed_out=True, optimal_proven=False)
        documents, messages = await self.run_handler(data, result)
        self.assertIn("غير مثبت", documents[0][1])
        self.assertIn("قبل إثبات أنه الأفضل", messages.call_args_list[0].kwargs["text"])

    def test_incomplete_results_are_explicit(self):
        partial = solve(courses([meeting()], [meeting()]))
        self.assertIn("تعذّر جمع كل المواد", bot.build_optimized_summary_text(partial))
        self.assertNotIn("تعارض حتمي", bot.build_optimized_summary_text(partial))
        missing = solve(courses([meeting()], []))
        self.assertIn("بدون معلومات أوقات مكتملة وصالحة", bot.build_optimized_summary_text(missing))


if __name__ == "__main__":
    unittest.main()
