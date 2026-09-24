#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notifier_admin.py
------------------
أوامر المراجعة والإدارة، وتعمل داخل **شات الإشعارات نفسه** (بوت الإشعارات
المنفصل)، لا داخل بوت الجدول الذي يستخدمه الطلاب. الهدف أن تستعرض بيانات
المستخدمين وتُكملها من تيليغرام مباشرة دون لمس Redis أو أي لوحة خارجية.

الأوامر المتاحة (للمالك فقط):
    /help                          هذه القائمة
    /stats                         الإحصائيات العامة (محتوى اللوحة نفسه)
    /users                         آخر 20 مستخدمًا انضمامًا
    /user <معرّف|رقم|@اسم>         بطاقة كاملة لمستخدم، مع سجل أحداثه
    /note <معرّف|رقم> <نص>         ملاحظة مراجعة تُحفظ على سجل المستخدم
    /export csv | /export json     تفريغ كل البيانات كملف
    /reindex                       إعادة بناء الفهارس من السجلات نفسها

الأمان
------
كل أمر يتحقق أن الشات هو NOTIFIER_CHAT_ID نفسه، وأي طلب من أي شات آخر يُتجاهَل
ويُسجَّل في الـ logs. بيانات المستخدمين (أسماء، معرّفات، أنماط استخدام) لا يجب
أن تُعرض إلا عليك.

التشغيل
-------
يعمل بوت الإدارة بـ long polling في خيط مستقل بحلقة أحداث خاصة به، في الحالتين
معًا (تشغيل محلي ونشر على Render). هذا مقصود:

1) لا نستخدم Application.run_polling() الجاهزة لأنها تسجّل معالجات إشارات
   عبر loop.add_signal_handler، ولا تلتقط إلا NotImplementedError — بينما
   الاستدعاء من خيط غير رئيسي على لينكس يرفع ValueError فيُسقط الخيط.
   لذلك نُشغّل initialize/start/start_polling يدويًا.

2) لا نحتاج webhook ثانيًا لبوت الإشعارات، فيبقى bot.py بلا مسار إضافي وبلا
   متغير سرّية جديد على Render.

تحذير ضروري: إن كان NOTIFIER_BOT_TOKEN هو نفسه توكن بوت الجدول، فإن تشغيل
مستمعَين لبوت واحد يعني أن تيليغرام سيسلّم التحديثات لأحدهما فقط بشكل غير
متوقع. لذلك نرفض التشغيل في هذه الحالة ونسجّل تحذيرًا واضحًا.
"""

import asyncio
import io
import logging
import threading
from datetime import datetime

from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes

import notifier

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
MAX_EVENTS_SHOWN = 15
DEFAULT_USER_LIST_SIZE = 20


# ---------------------------------------------------------------------------
# حصر الأوامر بالمالك
# ---------------------------------------------------------------------------

def _is_owner_chat(update: Update) -> bool:
    allowed = notifier.NOTIFIER_CHAT_ID
    if not allowed or update.effective_chat is None:
        return False
    return str(update.effective_chat.id) == str(allowed)


def owner_only(handler):
    """يزخرف معالج أمر ليتجاهل أي طلب من شات غير شات المالك."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_owner_chat(update):
            logger.warning(
                "أمر إدارة مرفوض من شات غير مصرّح له: %s",
                update.effective_chat.id if update.effective_chat else "?",
            )
            return
        return await handler(update, context)

    wrapper.__name__ = getattr(handler, "__name__", "handler")
    return wrapper


# ---------------------------------------------------------------------------
# أدوات الرد
# ---------------------------------------------------------------------------

async def _reply_long(message, text):
    """يرسل نصًا طويلًا مقطّعًا: تيليغرام يرفض أي رسالة تتجاوز 4096 محرفًا،
    وسجل أحداث مستخدم نشط يتجاوز هذا بسهولة."""
    for start in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
        await message.reply_text(text[start:start + TELEGRAM_MESSAGE_LIMIT])


def _user_line(record, now=None):
    number = record.get("user_number")
    online = "🟢" if notifier.is_online(record, now) else "⚪"
    username = record.get("username")
    handle = f"@{username}" if username else "بلا @"
    full_name = record.get("full_name")
    suffix = f" — {full_name}" if full_name and full_name != username else ""
    return f"{online} #{number} {handle}{suffix} ({record.get('telegram_id')})"


def _format_actions(record):
    actions = record.get("actions") or {}
    if not actions:
        return ["  (لا إجراءات مسجّلة بعد)"]
    lines = []
    for action_type, count in sorted(actions.items(), key=lambda item: -item[1]):
        label = notifier.ACTION_LABELS.get(action_type, action_type)
        lines.append(f"  {label}: {count}")
    return lines


def _format_events(record):
    events = record.get("events") or []
    if not events:
        return []
    shown = events[-MAX_EVENTS_SHOWN:]
    # صياغة ثابتة لا تعتمد على العدد: جمع الأعداد في العربية يتغير بين
    # المفرد والمثنى والجمع، وعنوان كهذا يظهر بأعداد عشوائية باستمرار.
    lines = [f"آخر الأحداث ({len(shown)} من {len(events)} محفوظة):"]
    for event in shown:
        stamp = event.get("time") or event.get("t") or "-"
        label = notifier.ACTION_LABELS.get(event.get("type"), event.get("type"))
        value = event.get("value")
        lines.append(f"  {stamp}  {label}" + (f" = {value}" if value else ""))
    return lines


# ---------------------------------------------------------------------------
# الأوامر
# ---------------------------------------------------------------------------

@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "أوامر مراجعة بيانات المستخدمين:\n\n"
        "/stats — الإحصائيات العامة\n"
        "/users — آخر 20 مستخدمًا انضمامًا\n"
        "/user <معرّف أو رقم أو @اسم> — بطاقة مستخدم كاملة\n"
        "/note <معرّف أو رقم> <نص> — مراجعة/إكمال بيانات المستخدم\n"
        "/export csv أو /export json — تفريغ كل البيانات كملف\n"
        "/reindex — إعادة بناء الفهارس من السجلات\n\n"
        "الرقم الدائم (#) لا يتغير أبدًا، ويمكن استخدامه بدل معرّف تيليغرام "
        "في /user و/note."
    )


@owner_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await asyncio.to_thread(
        lambda: notifier.build_dashboard_text(notifier.get_stats())
    )
    await _reply_long(update.effective_message, text)


@owner_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    limit = DEFAULT_USER_LIST_SIZE
    if context.args:
        try:
            limit = max(1, min(100, int(context.args[0])))
        except ValueError:
            pass

    records, now = await asyncio.to_thread(
        lambda: (notifier.list_recent_users(limit), notifier.now_damascus())
    )
    if not records:
        await update.effective_message.reply_text("لا يوجد مستخدمون مسجّلون بعد.")
        return

    lines = [f"المستخدمون الأحدث انضمامًا ({len(records)}):", ""]
    lines.extend(_user_line(record, now) for record in records)
    await _reply_long(update.effective_message, "\n".join(lines))


@owner_only
async def cmd_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text(
            "الاستخدام: /user <معرّف تيليغرام أو الرقم الدائم أو @اسم المستخدم>"
        )
        return

    identifier = " ".join(context.args)
    record = await asyncio.to_thread(notifier.find_user, identifier)
    if record is None:
        await update.effective_message.reply_text(f"لم يُعثر على مستخدم: {identifier}")
        return

    now = notifier.now_damascus()
    lines = [_user_line(record, now), ""]
    lines.append(f"الاسم الكامل: {record.get('full_name') or '-'}")
    lines.append(f"انضم: {record.get('join_date') or '-'}")

    last_epoch = record.get("last_active_epoch")
    if last_epoch:
        last_dt = datetime.fromtimestamp(float(last_epoch), notifier.DAMASCUS_TZ)
        lines.append(f"آخر نشاط: {notifier.format_last_active(last_dt)}")
    else:
        lines.append("آخر نشاط: -")

    lines.append("")
    lines.append("الإجراءات:")
    lines.extend(_format_actions(record))
    lines.append(f"  الإجمالي: {record.get('total_actions', 0)}")

    if record.get("note"):
        lines.append("")
        lines.append(f"📝 ملاحظة: {record['note']}")

    events_block = _format_events(record)
    if events_block:
        lines.append("")
        lines.extend(events_block)

    await _reply_long(update.effective_message, "\n".join(lines))


@owner_only
async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يحفظ ملاحظة على سجل مستخدم — هذه هي أداة "المراجعة والإكمال لاحقًا":
    تفتح بطاقة المستخدم، تكتب ما لاحظته، فتبقى الملاحظة ظاهرة في رسالته وفي
    /user حتى تعود إليها لاحقًا."""
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text(
            "الاستخدام: /note <معرّف أو رقم> <نص الملاحظة>"
        )
        return

    identifier, note = context.args[0], " ".join(context.args[1:]).strip()
    record = await asyncio.to_thread(notifier.find_user, identifier)
    if record is None:
        await update.effective_message.reply_text(f"لم يُعثر على مستخدم: {identifier}")
        return

    is_delete = note.lower() in {"-", "delete", "حذف"}
    saved = await asyncio.to_thread(
        notifier.set_note, record["telegram_id"], "" if is_delete else note
    )
    if not saved:
        await update.effective_message.reply_text("تعذّر حفظ الملاحظة (السجل غير موجود).")
        return

    if is_delete:
        await update.effective_message.reply_text(
            f"حُذفت الملاحظة عن #{record.get('user_number')}."
        )
        return

    await update.effective_message.reply_text(
        f"حُفظت الملاحظة على #{record.get('user_number')} "
        f"({record.get('username') or record['telegram_id']})."
    )


@owner_only
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fmt = (context.args[0].lower() if context.args else "csv")
    if fmt not in {"csv", "json"}:
        await update.effective_message.reply_text("الاستخدام: /export csv أو /export json")
        return

    if fmt == "csv":
        data, filename = await asyncio.to_thread(
            lambda: (notifier.export_csv(),
                     f"webseeker_users_{datetime.now():%Y%m%d_%H%M}.csv")
        )
        caption = "بيانات المستخدمين (CSV — يفتح في Excel)."
    else:
        data, filename = await asyncio.to_thread(
            lambda: (notifier.export_json(),
                     f"webseeker_users_{datetime.now():%Y%m%d_%H%M}.json")
        )
        caption = "بيانات المستخدمين (JSON — نسخة كاملة مع الإحصائيات)."

    await update.effective_message.reply_document(
        document=io.BytesIO(data),
        filename=filename,
        caption=caption,
    )


@owner_only
async def cmd_reindex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("جارٍ إعادة بناء الفهارس...")
    count = await asyncio.to_thread(notifier.rebuild_indexes)
    await asyncio.to_thread(notifier.refresh_dashboard, True)
    await update.effective_message.reply_text(
        f"تم. أُعيد بناء الفهارس من السجلات المحفوظة ({count})."
    )


# ---------------------------------------------------------------------------
# التشغيل
# ---------------------------------------------------------------------------

async def _on_error(update, context: ContextTypes.DEFAULT_TYPE):
    """بدون معالج أخطاء تطبع المكتبة Traceback كاملًا دون أي إشارة لأي بوت
    صدر الخطأ. هنا نوسم الخطأ باسم بوت الإشعارات، فيُعرف فورًا في الـ Logs
    أن التعارض على NOTIFIER_BOT_TOKEN وليس على توكن بوت الجدول."""
    if isinstance(context.error, Conflict):
        logger.warning(
            "[notifier-admin] Conflict: مستمع آخر يستخدم NOTIFIER_BOT_TOKEN "
            "بـ getUpdates الآن (نسخة قديمة أو جهاز آخر أو الموقع)."
        )
        return
    logger.error("[notifier-admin] خطأ غير متوقع", exc_info=context.error)


def _build_application():
    app = Application.builder().token(notifier.NOTIFIER_BOT_TOKEN).build()
    app.add_error_handler(_on_error)
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("user", cmd_user))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("reindex", cmd_reindex))
    return app


def _on_polling_error(exc):
    """يُمرَّر إلى start_polling: الـ Updater يسجّل أخطاء getUpdates بنفسه (وبـ
    Traceback كامل) ولا يمرّ بمعالجات الأخطاء العادية، فلا بد من هذا الاستدعاء
    المباشر. دالة عادية (لا async) كما تشترط المكتبة."""
    if isinstance(exc, Conflict):
        logger.warning(
            "[notifier-admin] Conflict: مستمع آخر يستخدم NOTIFIER_BOT_TOKEN "
            "بـ getUpdates الآن."
        )
        return
    logger.error("[notifier-admin] خطأ أثناء polling", exc_info=exc)


async def _run_polling(app):
    """يشغّل التطبيق دون Application.run_polling() — انظر الشرح أعلى الملف."""
    await app.initialize()
    await app.start()
    # drop_pending_updates=False مقصود: على خطة Render المجانية تنام الخدمة،
    # وقد يرسل المالك أمرًا أثناء نومها. إسقاط التحديثات المعلّقة يعني ضياع
    # ذلك الأمر بصمت، بينما إبقاؤها يضمن معالجته عند أول إقلاع.
    await app.updater.start_polling(
        allowed_updates=["message"],
        drop_pending_updates=False,
        error_callback=_on_polling_error,
    )
    logger.info("بوت أوامر الإدارة يعمل (long polling).")
    while True:
        await asyncio.sleep(3600)


def start_in_background(main_bot_token=""):
    """يشغّل بوت أوامر الإدارة في خيط مستقل. يعيد الخيط أو None إن لم يُشغَّل."""
    if not notifier.NOTIFIER_BOT_TOKEN:
        logger.warning(
            "أوامر الإدارة غير مُفعَّلة: لم يتم ضبط NOTIFIER_BOT_TOKEN."
        )
        return None

    if not notifier.NOTIFIER_CHAT_ID:
        logger.warning(
            "أوامر الإدارة غير مُفعَّلة: لم يتم ضبط NOTIFIER_CHAT_ID "
            "(لا يوجد شات مالك للتحقق من الصلاحية)."
        )
        return None

    if main_bot_token and main_bot_token == notifier.NOTIFIER_BOT_TOKEN:
        logger.error(
            "NOTIFIER_BOT_TOKEN هو نفسه توكن بوت الجدول. لا يمكن تشغيل "
            "مستمعَين لبوت واحد (ستضيع تحديثات أحدهما). أنشئ بوتًا منفصلًا "
            "من @BotFather للإشعارات، أو اترك المتغير فارغًا لتعطيل أوامر الإدارة."
        )
        return None

    def _thread_main():
        try:
            asyncio.run(_run_polling(_build_application()))
        except Exception:  # noqa: BLE001
            logger.exception("توقف بوت أوامر الإدارة بشكل غير متوقع")

    thread = threading.Thread(target=_thread_main, name="notifier-admin", daemon=True)
    thread.start()
    return thread
