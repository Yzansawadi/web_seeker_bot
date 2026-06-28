#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notifier.py
------------
يرسل إشعارات فورية إلى بوت تيليغرام خاص بمالك المشروع (أنت)، بحيث يكون
لكل مستخدم لبوت الجدول **رسالة واحدة ثابتة** في شات الإشعارات، تُعدَّل
(لا تُكرَّر) عند كل حدث جديد، فتطفو تلقائيًا لأسفل الشات كأحدث رسالة.

التخزين الدائم:
    بيانات كل مستخدم (معلوماته + سجل أحداثه + رقم رسالته في تيليغرام)
    تُخزَّن في قاعدة Upstash Redis (REST API)، لا على قرص Render، لأن
    قرص Render مؤقت ويُمحى عند أي إعادة نشر -- بينما Upstash يبقى دائمًا
    بلا أي علاقة بدورة حياة خدمة Render.

الإعداد (متغيرات بيئة، تُضبط على Render كقيم محمية):
    NOTIFIER_BOT_TOKEN     : توكن بوت تيليغرام منفصل، خاص بالإشعارات فقط.
    NOTIFIER_CHAT_ID       : معرّفك الشخصي على تيليغرام (من @userinfobot).
    UPSTASH_REDIS_REST_URL : رابط REST لقاعدة Upstash Redis.
    UPSTASH_REDIS_REST_TOKEN: التوكن المرافق لها.

إن لم تُضبط هذه القيم، تُكتب الإشعارات في الـ logs فقط دون إرسال أو
تخزين أي شيء فعليًا -- بوت الجدول الأساسي يستمر بالعمل بشكل طبيعي دون أي
اعتماد على هذه الوحدة.
"""

import os
import json
import logging
import threading
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

NOTIFIER_BOT_TOKEN = os.environ.get("NOTIFIER_BOT_TOKEN", "")
NOTIFIER_CHAT_ID = os.environ.get("NOTIFIER_CHAT_ID", "")
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

_notify_enabled = bool(NOTIFIER_BOT_TOKEN and NOTIFIER_CHAT_ID)
_storage_enabled = bool(UPSTASH_URL and UPSTASH_TOKEN)

if not _notify_enabled:
    logger.warning(
        "إشعارات تيليغرام الخاصة بالمالك غير مُفعَّلة "
        "(لم يتم ضبط NOTIFIER_BOT_TOKEN أو NOTIFIER_CHAT_ID)."
    )
if not _storage_enabled:
    logger.warning(
        "التخزين الدائم (Upstash Redis) غير مُفعَّل "
        "(لم يتم ضبط UPSTASH_REDIS_REST_URL أو UPSTASH_REDIS_REST_TOKEN)."
    )

DAMASCUS_TZ = timezone(timedelta(hours=3))  # توقيت دمشق/بيروت تقريبًا (UTC+3)

MAX_EVENTS_KEPT = 30  # أقصى عدد أحداث محفوظة في سجل النشاط الحالي لكل مستخدم

TOTAL_USERS_COUNTER_KEY = "iust_total_users_counter"

# ---------------------------------------------------------------------------
# قفل لكل مستخدم: يمنع معالجة حدثين لنفس المستخدم بالتوازي (race condition).
# bot.py يُطلق كل حدث كمهمة خلفية مستقلة (asyncio.to_thread)، فإذا ضغط
# المستخدم زرين بسرعة (أو تأخر طلب شبكي عن آخر)، يمكن لمهمتين أن تقرآ نفس
# السجل القديم من Redis في نفس اللحظة، فتفقد إحدى التعديلات وتُرسَل رسالتان
# منفصلتان بدل واحدة (وهذا ما تسبب بمشكلة التكرار وتذبذب total_users).
# القفل هنا يضمن أن أحداث المستخدم نفسه تُعالَج بترتيب تسلسلي صارم.
# ---------------------------------------------------------------------------
_user_locks = {}
_user_locks_guard = threading.Lock()


def _get_user_lock(user_id):
    with _user_locks_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _user_locks[user_id] = lock
        return lock


# ---------------------------------------------------------------------------
# تخزين Upstash Redis (REST API بسيط -- لا حاجة لمكتبة redis التقليدية)
# ---------------------------------------------------------------------------

def _redis_call(*command_parts):
    """ينفّذ أمر Redis واحدًا عبر REST API. يعيد None عند أي فشل أو إن كان
    التخزين غير مُفعَّل، حتى لا يتسبب أي خطأ هنا بتعطيل بوت الجدول الأساسي."""
    if not _storage_enabled:
        return None
    try:
        response = requests.post(
            UPSTASH_URL,
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            json=list(command_parts),
            timeout=10,
        )
        if response.status_code != 200:
            logger.warning("فشل طلب Upstash Redis (الحالة %s): %s",
                            response.status_code, response.text[:300])
            return None
        return response.json().get("result")
    except requests.RequestException:
        logger.exception("خطأ في الاتصال بـ Upstash Redis")
        return None


def _user_key(user_id):
    return f"iust_user:{user_id}"


def load_user_record(user_id):
    """يقرأ سجل مستخدم من Redis. يعيد None إن لم يكن موجودًا أو عند الفشل."""
    raw = _redis_call("GET", _user_key(user_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def save_user_record(user_id, record):
    _redis_call("SET", _user_key(user_id), json.dumps(record, ensure_ascii=False))


def _increment_total_users():
    """
    يزيد عدّاد المستخدمين الكلي الدائم على Redis بمقدار 1 ويعيد القيمة
    الجديدة. يُستخدم INCR لأنه أمر ذرّي (atomic) بطبيعته في Redis، فلا
    يحدث تضارب حتى لو استدعاه عدة مستخدمين جدد في نفس اللحظة بالضبط --
    على عكس "قراءة len(seen_users) من الذاكرة" الذي كان يُصفَّر أساسًا مع
    كل إعادة تشغيل لخدمة Render ويعطي أرقامًا غير متّسقة بين المستخدمين.
    """
    result = _redis_call("INCR", TOTAL_USERS_COUNTER_KEY)
    return result if result is not None else "-"


def _get_total_users():
    """يقرأ العدّاد الكلي الحالي دون زيادته (لعرضه في رسائل لا تخص مستخدمًا جديدًا)."""
    result = _redis_call("GET", TOTAL_USERS_COUNTER_KEY)
    if result is None:
        return "-"
    try:
        return int(result)
    except (TypeError, ValueError):
        return "-"


# ---------------------------------------------------------------------------
# أدوات تنسيق
# ---------------------------------------------------------------------------

def _now_damascus():
    return datetime.now(DAMASCUS_TZ)


def _format_datetime(dt):
    return dt.strftime("%Y-%m-%d %I:%M:%S %p")


def _format_last_active(dt):
    now = _now_damascus()
    label = "today" if dt.date() == now.date() else dt.strftime("%Y-%m-%d")
    return f"{label} / {dt.strftime('%I:%M:%S %p')}"


def _build_message_text(record):
    lines = [
        f"total_users : {_get_total_users()}",
        f"telegram_id: {record['telegram_id']}",
        f"username: {record.get('username') or '-'}",
        f"join_date: {record['join_date']}",
        f"last_active: {_format_last_active(datetime.fromisoformat(record['last_active_iso']))}",
        f"number of schedules created : {record.get('schedules_created', 0)}",
        "_______________________",
    ]
    events = record.get("events", [])
    for i, ev in enumerate(events, start=1):
        lines.append(f"event {i} :\t{ev['type']}")
        if ev.get("value"):
            lines.append(f"value :\t{ev['value']}")
        lines.append("__")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# إرسال/تعديل الرسالة في تيليغرام
# ---------------------------------------------------------------------------

def _telegram_api(method, payload):
    if not _notify_enabled:
        return None
    url = f"https://api.telegram.org/bot{NOTIFIER_BOT_TOKEN}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        if not data.get("ok"):
            logger.warning("فشل استدعاء تيليغرام %s: %s", method, data)
            return None
        return data.get("result")
    except requests.RequestException:
        logger.exception("خطأ في الاتصال أثناء استدعاء تيليغرام %s", method)
        return None


def _send_new_message(text):
    result = _telegram_api("sendMessage", {"chat_id": NOTIFIER_CHAT_ID, "text": text})
    return result.get("message_id") if result else None


def _delete_message(message_id):
    # عدم التحقق من نجاح الحذف عمدًا: إن كانت الرسالة محذوفة مسبقًا أو
    # قديمة جدًا، فشل الحذف لا يهم -- المهم إرسال الرسالة الجديدة بعده.
    _telegram_api("deleteMessage", {"chat_id": NOTIFIER_CHAT_ID, "message_id": message_id})


def _push_to_telegram(record):
    """
    يحذف رسالة المستخدم القديمة (إن وُجدت) ويرسل رسالة جديدة بالمحتوى
    المحدَّث، ويحدّث record['message_id'] في الذاكرة بالقيمة الجديدة.

    لا تحفظ هذه الدالة السجل في Redis بنفسها (الحفظ مسؤولية المستدعي،
    مرة واحدة بكل التغييرات مجتمعة) لتفادي استدعاء SET مرتين لكل حدث.

    نستخدم حذف+إرسال عمدًا بدل تعديل الرسالة في مكانها: تعديل رسالة
    قديمة (editMessageText) يُحدّث محتواها فقط، لكنه لا "يرفعها" لأسفل
    المحادثة أبدًا -- وهذا يعني أن نشاط مستخدم قديم يبقى مخفيًا في أعلى
    الشات إذا تراكمت رسائل مستخدمين آخرين بعده. حذف الرسالة وإرسال رسالة
    جديدة بمحتواها يضمن ظهورها دائمًا في آخر المحادثة عند أي نشاط جديد.
    """
    old_message_id = record.get("message_id")
    text = _build_message_text(record)

    if old_message_id:
        _delete_message(old_message_id)

    new_id = _send_new_message(text)
    if new_id:
        record["message_id"] = new_id


# ---------------------------------------------------------------------------
# الواجهة العامة المستخدمة من bot.py
# ---------------------------------------------------------------------------

def register_new_user(user_id, username, total_users_at_join=None):
    """
    يُستدعى أول مرة يظهر فيها مستخدم جديد. ينشئ سجلًا جديدًا له ويرسل
    أول رسالة خاصة به في شات الإشعارات.

    total_users_at_join: معامل قديم محتفَظ به للتوافق مع استدعاءات
    سابقة، لكنه غير مُستخدَم فعليًا -- العدّاد الكلي يُحسَب الآن من Redis
    نفسه عبر _increment_total_users() ليكون دائمًا ومتّسقًا، بدل الاعتماد
    على عدّاد في الذاكرة يُصفَّر مع كل إعادة تشغيل لخدمة Render.
    """
    with _get_user_lock(user_id):
        now = _now_damascus()
        record = {
            "telegram_id": user_id,
            "username": username,
            "join_date": now.strftime("%Y-%m-%d"),
            "last_active_iso": now.isoformat(),
            "schedules_created": 0,
            "events": [],
            "message_id": None,
        }
        _increment_total_users()
        _push_to_telegram(record)  # يضبط record["message_id"] في الذاكرة
        save_user_record(user_id, record)  # حفظ نهائي واحد بكل القيم مجتمعة


def log_event(user_id, username, event_type, value=""):
    """
    يضيف حدثًا جديدًا لسجل المستخدم ويحدّث رسالته في تيليغرام. إن لم يكن
    لهذا المستخدم سجل سابق (مثلًا أُعيد تشغيل البوت قبل أول استدعاء track_user
    من نوع آخر)، يُنشأ سجل جديد بشكل تلقائي حتى لا يُفقد الحدث.

    حالة خاصة: إذا كان event_type هو "show_schedule"، يُزاد عدّاد الجداول
    المُنشأة، ثم (حسب التعليمات) يُفرَّغ سجل الأحداث الحالي بالكامل بعد
    تسجيل هذا الحدث، استعدادًا لجلسة جديدة.

    محاط بقفل خاص بهذا المستخدم: لأن bot.py يُطلق كل حدث كمهمة خلفية
    مستقلة، قد تصل أحداث متعددة لنفس المستخدم بالتوازي (ضغطتا زر سريعتان
    مثلاً)؛ القفل يضمن معالجتها بترتيب تسلسلي، فلا يُفقد أي تعديل ولا
    تُرسَل رسالتان منفصلتان لحدثين متزامنين.
    """
    with _get_user_lock(user_id):
        record = load_user_record(user_id)
        now = _now_damascus()

        if record is None:
            record = {
                "telegram_id": user_id,
                "username": username,
                "join_date": now.strftime("%Y-%m-%d"),
                "last_active_iso": now.isoformat(),
                "schedules_created": 0,
                "events": [],
                "message_id": None,
            }

        record["last_active_iso"] = now.isoformat()
        record["username"] = username or record.get("username")

        is_show_schedule = event_type == "show_schedule"
        if is_show_schedule:
            record["schedules_created"] = record.get("schedules_created", 0) + 1

        record.setdefault("events", []).append({"type": event_type, "value": value})
        record["events"] = record["events"][-MAX_EVENTS_KEPT:]

        # بعد تسجيل حدث "عرض الجدول" نفسه، نفرّغ سجل الأحداث فورًا (في نفس
        # السجل قبل الحفظ، لا بعده) ليبدأ نشاط المستخدم القادم من جديد، كما
        # طُلب تحديدًا. هذا يجمع الإضافة والتصفير في عملية حفظ ودفع واحدة فقط
        # بدل مرتين متتاليتين (توفير فعلي على عدد أوامر Redis وعدد طلبات
        # تيليغرام لكل ضغطة "عرض الجدول").
        if is_show_schedule:
            record["events"] = []

        _push_to_telegram(record)  # يضبط record["message_id"] الجديد في الذاكرة
        save_user_record(user_id, record)  # حفظ نهائي واحد بكل القيم مجتمعة


def notify_update_result(success: bool, detail: str = "") -> None:
    """إشعار عام (ليس مرتبطًا بمستخدم محدد) بنتيجة تحديث أوقات الجدول.
    يُرسَل كرسالة جديدة منفصلة كل مرة، لأنه ليس جزءًا من متابعة مستخدم."""
    if success:
        text = "تحديث أوقات الجدول: تم بنجاح."
    else:
        text = "تحديث أوقات الجدول: فشل."
        if detail:
            text += f"\nالتفاصيل: {detail}"
    _send_new_message(text)
