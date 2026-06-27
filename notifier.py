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
        f"total_users : {record.get('total_users_at_join', '-')}",
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


def _edit_message(message_id, text):
    result = _telegram_api(
        "editMessageText",
        {"chat_id": NOTIFIER_CHAT_ID, "message_id": message_id, "text": text},
    )
    return result is not None


def _push_to_telegram(record):
    """
    يعدّل رسالة المستخدم الموجودة إن أمكن. إن فشل التعديل (مثلًا الرسالة
    قديمة جدًا، أو حُذفت، أو لم تُرسَل رسالة من قبل)، يرسل رسالة جديدة
    ويحفظ رقمها الجديد في السجل.
    """
    message_id = record.get("message_id")
    text = _build_message_text(record)

    if message_id and _edit_message(message_id, text):
        return

    new_id = _send_new_message(text)
    if new_id:
        record["message_id"] = new_id
        save_user_record(record["telegram_id"], record)


# ---------------------------------------------------------------------------
# الواجهة العامة المستخدمة من bot.py
# ---------------------------------------------------------------------------

def register_new_user(user_id, username, total_users_at_join):
    """يُستدعى أول مرة يظهر فيها مستخدم جديد. ينشئ سجلًا جديدًا له ويرسل
    أول رسالة خاصة به في شات الإشعارات."""
    now = _now_damascus()
    record = {
        "telegram_id": user_id,
        "username": username,
        "join_date": now.strftime("%Y-%m-%d"),
        "last_active_iso": now.isoformat(),
        "total_users_at_join": total_users_at_join,
        "schedules_created": 0,
        "events": [],
        "message_id": None,
    }
    save_user_record(user_id, record)
    _push_to_telegram(record)


def log_event(user_id, username, event_type, value=""):
    """
    يضيف حدثًا جديدًا لسجل المستخدم ويحدّث رسالته في تيليغرام. إن لم يكن
    لهذا المستخدم سجل سابق (مثلًا أُعيد تشغيل البوت قبل أول استدعاء track_user
    من نوع آخر)، يُنشأ سجل جديد بشكل تلقائي حتى لا يُفقد الحدث.

    حالة خاصة: إذا كان event_type هو "show_schedule"، يُزاد عدّاد الجداول
    المُنشأة، ثم (حسب التعليمات) يُفرَّغ سجل الأحداث الحالي بالكامل بعد
    تسجيل هذا الحدث، استعدادًا لجلسة جديدة.
    """
    record = load_user_record(user_id)
    now = _now_damascus()

    if record is None:
        record = {
            "telegram_id": user_id,
            "username": username,
            "join_date": now.strftime("%Y-%m-%d"),
            "last_active_iso": now.isoformat(),
            "total_users_at_join": "-",
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

    save_user_record(user_id, record)
    _push_to_telegram(record)

    # بعد تسجيل حدث "عرض الجدول" نفسه (يظهر في الرسالة)، نفرّغ السجل
    # ليبدأ نشاط المستخدم القادم من جديد، كما طُلب تحديدًا.
    if is_show_schedule:
        record["events"] = []
        save_user_record(user_id, record)
        _push_to_telegram(record)


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
