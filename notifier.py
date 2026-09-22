#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notifier.py
------------
نظام متابعة المستخدمين وإشعارات المالك.

الفكرة الأساسية
---------------
لكل مستخدم **رسالة واحدة ثابتة** في شات الإشعارات، تُحدَّث ولا تتكرر: عند أي
نشاط جديد تُحذف رسالته القديمة وتُرسل رسالة جديدة بمحتواها المحدَّث، فتطفو
تلقائيًا لآخر الشات. ولأن ضغطات الأزرار السريعة كانت تُنتج سيلًا من الرسائل،
تمر كل عملية "دفع إلى تيليغرام" الآن عبر **مُرجِئ (debounce)**: البيانات تُحفَظ
في Redis فورًا دائمًا، أما الرسالة فتُرسل مرة واحدة بعد هدوء المستخدم بثوانٍ.

بالإضافة لرسائل المستخدمين، توجد **رسالة لوحة واحدة** (dashboard) تُعدَّل في
مكانها كل فترة، وتعرض الإحصائيات العامة ومن هو متصل الآن.

ما الذي يُتابَع لكل مستخدم
---------------------------
1. رقم تسلسلي دائم (`user_number`) يُخصَّص مرة واحدة عند أول ظهور ولا يتغير
   أبدًا بعدها، حتى لو أُعيد تشغيل البوت أو اختفى السجل من الذاكرة.
2. الاسم والمعرّف (@username) والاسم الكامل و telegram_id.
3. ثلاثة عدّادات منفصلة: جدول المواد المختارة، جدول كل المواد، الجدول المثالي.
4. حالة النشاط الحالي (🟢 متصل الآن / ⚪ غير نشط) المحسوبة من آخر نشاط.

التخزين الدائم
--------------
كل شيء في Upstash Redis (REST API). قرص Render مؤقت ويُمحى عند أي إعادة نشر،
بينما Redis يبقى. تُبنَى عدة فهارس في Redis لتجنّب مسح كل المفاتيح عند حساب
الإحصائيات:

    iust_user:{id}          سجل المستخدم (JSON)
    iust_total_users_counter عدّاد ذرّي، وهو نفسه مخصّص الأرقام الدائمة
    iust_users_index        ZSET: عضو = user_id، درجة = زمن الانضمام
    iust_presence           ZSET: عضو = user_id، درجة = زمن آخر نشاط
    iust_user_activity      ZSET: عضو = user_id، درجة = إجمالي إجراءاته
    iust_action_totals      HASH: نوع الحدث -> العدد الكلي على مستوى البوت
    iust_hour_histogram     HASH: ساعة اليوم (0-23) -> عدد الإجراءات
    iust_day_histogram      HASH: يوم الأسبوع (0-6) -> عدد الإجراءات
    iust_dashboard_*        معرّف رسالة اللوحة وبصمة آخر محتوى لها

الإعداد (متغيرات بيئة):
    NOTIFIER_BOT_TOKEN      توكن بوت تيليغرام منفصل خاص بالإشعارات.
    NOTIFIER_CHAT_ID        معرّف شات المالك (من @userinfobot).
    UPSTASH_REDIS_REST_URL  رابط REST لقاعدة Upstash Redis.
    UPSTASH_REDIS_REST_TOKEN التوكن المرافق لها.

إن لم تُضبط هذه القيم، تُكتب الإشعارات في الـ logs فقط، ويستمر بوت الجدول
الأساسي بالعمل بشكل طبيعي دون أي اعتماد على هذه الوحدة.
"""

import csv
import hashlib
import io
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

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

SCHEMA_VERSION = 2

# كم يبقى المستخدم معتبرًا "متصلًا الآن" بعد آخر نشاط له.
ONLINE_WINDOW_SECONDS = 3 * 60

# حدّ أدنى بين تحديثَي presence لنفس المستخدم في Redis. الضغط على الأزرار
# يحدث أسرع من هذا بكثير، ودقّة Presence بمقياس ثوانٍ لا فائدة منها لأن
# نافذة "الاتصال الآن" أصلًا ثلاث دقائق.
PRESENCE_THROTTLE_SECONDS = 20

# مهلة الهدوء قبل دفع رسالة المستخدم إلى تيليغرام: كل حدث جديد يلغي المؤقت
# ويعيد جدولته، فلا تُرسَل إلا رسالة واحدة بعد توقف المستخدم عن الضغط.
PUSH_DEBOUNCE_SECONDS = 4.0

MAX_EVENTS_KEPT = 50

# أنواع الأحداث التي تُعتبر "إنجازات" وتُعرض كعدادات رئيسية في الرسالة.
ACTION_SELECTED_SCHEDULE = "show_schedule"
ACTION_ALL_TIMES = "send_all_times_pdf"
ACTION_OPTIMIZED = "optimize_schedule"

COUNTER_LABELS = {
    ACTION_SELECTED_SCHEDULE: "جدول مواد مختارة",
    ACTION_ALL_TIMES: "جدول كل المواد",
    ACTION_OPTIMIZED: "جدول مثالي",
}

# كل أنواع الأحداث المعروفة، لأغراض العرض والإحصاء.
ACTION_LABELS = {
    "start_command": "أمر /start",
    "select_year": "فتح سنة دراسية",
    "select_subject": "اختيار مادة",
    "delete_subject": "حذف مادة",
    "back_button": "رجوع/رئيسية",
    "update_schedule": "تحديث بيانات الجدول",
    ACTION_SELECTED_SCHEDULE: COUNTER_LABELS[ACTION_SELECTED_SCHEDULE],
    ACTION_ALL_TIMES: COUNTER_LABELS[ACTION_ALL_TIMES],
    ACTION_OPTIMIZED: COUNTER_LABELS[ACTION_OPTIMIZED],
}

WEEKDAY_NAMES = [
    "الإثنين", "الثلاثاء", "الأربعاء", "الخميس",
    "الجمعة", "السبت", "الأحد",
]

TOTAL_USERS_COUNTER_KEY = "iust_total_users_counter"
USERS_INDEX_KEY = "iust_users_index"
PRESENCE_KEY = "iust_presence"
ACTIVITY_INDEX_KEY = "iust_user_activity"
ACTION_TOTALS_KEY = "iust_action_totals"
HOUR_HISTOGRAM_KEY = "iust_hour_histogram"
DAY_HISTOGRAM_KEY = "iust_day_histogram"
DASHBOARD_MESSAGE_KEY = "iust_dashboard_message_id"
DASHBOARD_HASH_KEY = "iust_dashboard_text_hash"

# ---------------------------------------------------------------------------
# أقفال وحالة محلية
# ---------------------------------------------------------------------------
# bot.py يُطلق كل حدث كمهمة خلفية مستقلة (asyncio.to_thread). لولا القفل
# لقرأت مهمتان متزامنتان لنفس المستخدم نفس السجل القديم من Redis، فتُفقد
# إحداهما وتُرسَل رسالتان بدل واحدة. القفل يضمن تسلسلًا صارمًا لكل مستخدم.
_user_locks = {}
_user_locks_guard = threading.Lock()

# آخر زمن حدّثنا فيه حضور كل مستخدم (للخنق الزمني).
_presence_last_write = {}

# مؤقّتات الدفع المؤجَّل لكل مستخدم، مع أحدث لقطة من سجله.
_push_timers = {}
_pending_records = {}


def _get_user_lock(user_id):
    with _user_locks_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _user_locks[user_id] = lock
        return lock


# ---------------------------------------------------------------------------
# طبقة Redis (REST API)
# ---------------------------------------------------------------------------

def _redis_call(*command_parts):
    """ينفّذ أمر Redis واحدًا. يعيد None عند أي فشل أو إن كان التخزين غير
    مُفعَّل، حتى لا يُعطّل أي خطأ هنا بوت الجدول الأساسي."""
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


def _redis_pipeline(commands):
    """ينفّذ عدة أوامر Redis في طلب HTTP واحد عبر نقطة /pipeline في Upstash.

    هذا هو الفرق العملي بين النظام القديم والجديد: تحديث السجل + كل الفهارس
    + الإحصائيات العامة كان سيكلّف خمسة طلبات منفصلة لكل ضغطة زر، فأصبح
    طلبًا واحدًا. يعيد قائمة النتائج بنفس ترتيب الأوامر، أو None عند الفشل.
    """
    if not _storage_enabled or not commands:
        return None
    try:
        response = requests.post(
            f"{UPSTASH_URL}/pipeline",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            json=[list(c) for c in commands],
            timeout=15,
        )
        if response.status_code != 200:
            logger.warning("فشل طلب Upstash pipeline (الحالة %s): %s",
                           response.status_code, response.text[:300])
            return None
        data = response.json()
        # Upstash يعيد الشكل {"results": [{"status":..., "result":...}, ...]}
        # وبعض النسخ تعيد {"result": [...]} مباشرة، فندعم الاثنين.
        if isinstance(data.get("results"), list):
            return [item.get("result") for item in data["results"]]
        return data.get("result")
    except requests.RequestException:
        logger.exception("خطأ في الاتصال بـ Upstash Redis (pipeline)")
        return None


def _user_key(user_id):
    return f"iust_user:{user_id}"


def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _hash_to_int_dict(raw):
    """يحوّل ناتج HGETALL (قائمة مسطّحة [مفتاح، قيمة، ...]) إلى قاموس أعداد."""
    result = {}
    if not isinstance(raw, list):
        return result
    for i in range(0, len(raw) - 1, 2):
        key = str(raw[i])
        value = _to_int(raw[i + 1], 0)
        if value:
            result[key] = value
    return result


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
# الوقت والتنسيق
# ---------------------------------------------------------------------------

def now_damascus():
    return datetime.now(DAMASCUS_TZ)


def format_last_active(dt):
    now = now_damascus()
    label = "اليوم" if dt.date() == now.date() else dt.strftime("%Y-%m-%d")
    return f"{label} {dt.strftime('%I:%M:%S %p')}"


def _humanize_elapsed(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} ثانية"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} دقيقة" if secs < 30 else f"{minutes} د و{secs} ث"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ساعة" if minutes < 30 else f"{hours} س و{minutes} د"
    days, hours = divmod(hours, 24)
    return f"{days} يوم" if hours < 12 else f"{days} يوم و{hours} ساعة"


# ---------------------------------------------------------------------------
# إنشاء السجلات وترحيلها
# ---------------------------------------------------------------------------

def _allocate_user_number():
    """يخصّص رقمًا تسلسليًا دائمًا جديدًا.

    يُستخدم INCR لأنه أمر ذرّي في Redis: حتى لو وصل مستخدمان جديدان في نفس
    اللحظة بالضبط، لن يحصل أحدهما على رقم الآخر أو يشاركه فيه. وبما أن
    الرقم يُحفَظ داخل سجل المستخدم نفسه ولا يُشتَق أبدًا من ترتيب أو فهرس
    قابل لإعادة البناء، فهو لا يتغير طوال عمر الحساب.
    """
    result = _redis_call("INCR", TOTAL_USERS_COUNTER_KEY)
    return result if result is not None else 0


def _new_record(user_id, username=None, full_name=None, now=None):
    """ينشئ سجلًا جديدًا لمستخدم يظهر لأول مرة، مع رقمه الدائم."""
    now = now or now_damascus()
    epoch = now.timestamp()
    record = {
        "schema_version": SCHEMA_VERSION,
        "user_number": _allocate_user_number(),
        "telegram_id": user_id,
        "username": username or "",
        "full_name": full_name or "",
        "join_date": now.strftime("%Y-%m-%d"),
        "join_epoch": epoch,
        "last_active_iso": now.isoformat(),
        "last_active_epoch": epoch,
        "actions": {},
        "total_actions": 0,
        "events": [],
        "note": "",
        "message_id": None,
    }
    # يُضاف للفهرس فورًا حتى تظهر الإحصائيات (الجدد اليوم/هذا الأسبوع) صحيحة
    # حتى قبل أن يُجرى أي إحصاء يدوي.
    _redis_call("ZADD", USERS_INDEX_KEY, epoch, str(user_id))
    return record


def _migrate_record(user_id, record):
    """يرفع سجلًا قديمًا (من النسخة الأولى للنظام) إلى المخطط الحالي.

    السجلات القديمة لا تحمل رقمًا دائمًا ولا عدّادات مفصلة، وكانت تفقد كل
    بياناتها فعليًا مع كل إعادة تشغيل لأن bot.py كان يعيد إنشاءها. هنا نُكمل
    الناقص مرة واحدة بدل رمي التاريخ:
      - الأحداث القديمة في `events` تُحتسب وتُحوَّل إلى `actions`.
      - `schedules_created` القديم كان يزيد فقط عند show_schedule، فيُرحَّل
        إلى عدّاد "جدول مواد مختارة" دون أن يضيع.
      - الرقم الدائم يُخصَّص إن كان مفقودًا (بأثر رجعي لكل المستخدمين الحاليين).
      - فهرس الانضمام يُبنى من join_date إن كان السجل أقدم من وجود الفهرس.
    """
    if _to_int(record.get("schema_version"), 0) >= SCHEMA_VERSION and record.get("user_number"):
        return record

    actions = record.get("actions")
    if not isinstance(actions, dict):
        actions = {}
        for event in record.get("events") or []:
            event_type = event.get("type")
            if event_type:
                actions[event_type] = actions.get(event_type, 0) + 1
        legacy_created = _to_int(record.get("schedules_created"), 0)
        if legacy_created:
            # show_schedule كان يُصفّر events بعد كل جدول في النسخة القديمة،
            # فالعدّاد القديم هو المصدر الأدق لهذا النوع تحديدًا.
            actions[ACTION_SELECTED_SCHEDULE] = max(
                actions.get(ACTION_SELECTED_SCHEDULE, 0), legacy_created
            )

    record["schema_version"] = SCHEMA_VERSION
    record["actions"] = actions
    record["total_actions"] = sum(actions.values())
    record.setdefault("events", [])
    record.setdefault("note", "")
    record.setdefault("full_name", "")
    record.pop("schedules_created", None)

    if not record.get("user_number"):
        record["user_number"] = _allocate_user_number()

    join_epoch = record.get("join_epoch")
    if not join_epoch:
        try:
            join_epoch = datetime.strptime(
                record.get("join_date") or now_damascus().strftime("%Y-%m-%d"),
                "%Y-%m-%d",
            ).replace(tzinfo=DAMASCUS_TZ).timestamp()
        except ValueError:
            join_epoch = now_damascus().timestamp()
        record["join_epoch"] = join_epoch

    if not record.get("last_active_epoch"):
        record["last_active_epoch"] = _to_int(
            join_epoch, now_damascus().timestamp()
        )

    _redis_call("ZADD", USERS_INDEX_KEY, join_epoch, str(user_id))
    if record["total_actions"]:
        _redis_call("ZADD", ACTIVITY_INDEX_KEY, record["total_actions"], str(user_id))
    return record


def _touch_record(record, username=None, full_name=None, now=None):
    """يحدّث آخر نشاط والاسم (قد يتغير اسم المستخدم على تيليغرام مع الوقت)."""
    now = now or now_damascus()
    record["last_active_iso"] = now.isoformat()
    record["last_active_epoch"] = now.timestamp()
    if username:
        record["username"] = username
    if full_name:
        record["full_name"] = full_name
    return record


# ---------------------------------------------------------------------------
# الحضور (من متصل الآن)
# ---------------------------------------------------------------------------

def _record_presence(user_id, epoch):
    """يحدّث زمن آخر نشاط للمستخدم في فهرس الحضور، مع تنظيف المنتهين.

    نستخدم ZSET بدل علم (flag) لكل مستخدم لأن العلم يحتاج مهمة دورية
    لإطفائه، بينما هنا "الاتصال الآن" يُشتَقّ ببساطة من كون الدرجة ضمن
    النافذة الزمنية — فلا توجد حالة عالقة أبدًا إذا نامت خدمة Render.
    """
    cutoff = epoch - ONLINE_WINDOW_SECONDS
    _redis_pipeline([
        ["ZADD", PRESENCE_KEY, epoch, str(user_id)],
        ["ZREMRANGEBYSCORE", PRESENCE_KEY, "-inf", cutoff],
    ])


def _presence_throttled(user_id, now_ts):
    last = _presence_last_write.get(user_id, 0.0)
    if now_ts - last < PRESENCE_THROTTLE_SECONDS:
        return True
    _presence_last_write[user_id] = now_ts
    return False


def is_online(record, now=None):
    now = now or now_damascus()
    last = record.get("last_active_epoch")
    if not last:
        return False
    return (now.timestamp() - float(last)) <= ONLINE_WINDOW_SECONDS


# ---------------------------------------------------------------------------
# الإحصائيات العامة
# ---------------------------------------------------------------------------

def _log_activity_indexes(user_id, event_type, epoch, now):
    """حزمة الفهارس العامة لحدث واحد: إجراء كلي، نشاط المستخدم، ذروات الوقت.

    تُنفَّذ كلها في طلب HTTP واحد، وهو الفرق العملي عن النظام السابق الذي كان
    يستدعي Redis عدة مرات منفصلة لكل ضغطة زر.
    """
    _redis_pipeline([
        ["HINCRBY", ACTION_TOTALS_KEY, event_type, 1],
        ["HINCRBY", HOUR_HISTOGRAM_KEY, str(now.hour), 1],
        ["HINCRBY", DAY_HISTOGRAM_KEY, str(now.weekday()), 1],
        ["ZINCRBY", ACTIVITY_INDEX_KEY, 1, str(user_id)],
    ])


def _count_since(epoch):
    result = _redis_call("ZCOUNT", USERS_INDEX_KEY, epoch, "+inf")
    return _to_int(result, 0)


def _online_user_ids():
    cutoff = now_damascus().timestamp() - ONLINE_WINDOW_SECONDS
    _redis_call("ZREMRANGEBYSCORE", PRESENCE_KEY, "-inf", cutoff)
    raw = _redis_call("ZRANGE", PRESENCE_KEY, 0, -1)
    if not isinstance(raw, list):
        return []
    return [_to_int(item) for item in raw if _to_int(item) is not None]


def _load_records(user_ids):
    """يقرأ عدة سجلات في طلب واحد. يعيد قاموس {user_id: record}."""
    if not user_ids:
        return {}
    results = _redis_pipeline([["GET", _user_key(uid)] for uid in user_ids]) or []
    records = {}
    for uid, raw in zip(user_ids, results):
        if not raw:
            continue
        try:
            records[uid] = json.loads(raw)
        except (TypeError, ValueError):
            continue
    return records


def get_stats():
    """يجمع كل الإحصائيات العامة في قاموس واحد (تستخدمه اللوحة و/stats)."""
    now = now_damascus()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())

    total_users = _to_int(_redis_call("GET", TOTAL_USERS_COUNTER_KEY), 0) or 0
    indexed_users = _to_int(_redis_call("ZCARD", USERS_INDEX_KEY), 0) or 0

    action_totals = _hash_to_int_dict(_redis_call("HGETALL", ACTION_TOTALS_KEY))
    hour_hist = _hash_to_int_dict(_redis_call("HGETALL", HOUR_HISTOGRAM_KEY))
    day_hist = _hash_to_int_dict(_redis_call("HGETALL", DAY_HISTOGRAM_KEY))

    top_raw = _redis_call("ZREVRANGE", ACTIVITY_INDEX_KEY, 0, 4, "WITHSCORES") or []
    top_pairs = []
    top_ids = []
    if isinstance(top_raw, list):
        for i in range(0, len(top_raw) - 1, 2):
            uid = _to_int(top_raw[i])
            score = _to_int(top_raw[i + 1], 0)
            if uid is not None:
                top_pairs.append((uid, score))
                top_ids.append(uid)

    online_ids = _online_user_ids()
    online_records = _load_records(online_ids)
    top_records = _load_records(top_ids)

    peak_hour = max(hour_hist, key=hour_hist.get) if hour_hist else None
    peak_day = max(day_hist, key=day_hist.get) if day_hist else None

    return {
        "now": now,
        "total_users": max(total_users, indexed_users),
        "new_today": _count_since(today_start.timestamp()),
        "new_this_week": _count_since(week_start.timestamp()),
        "online_ids": online_ids,
        "online_records": online_records,
        "action_totals": action_totals,
        "total_actions": sum(action_totals.values()),
        "top_users": [
            {
                "user_id": uid,
                "actions": score,
                "record": top_records.get(uid),
            }
            for uid, score in top_pairs
        ],
        "peak_hour": _to_int(peak_hour),
        "peak_hour_count": hour_hist.get(str(peak_hour), 0) if peak_hour is not None else 0,
        "peak_day": _to_int(peak_day),
        "peak_day_count": day_hist.get(str(peak_day), 0) if peak_day is not None else 0,
    }


# ---------------------------------------------------------------------------
# بناء نصوص الرسائل
# ---------------------------------------------------------------------------

def _user_display_name(record):
    username = record.get("username")
    full_name = record.get("full_name")
    handle = f"@{username}" if username else "بلا @"
    if full_name and full_name != username:
        return f"{handle} | {full_name}"
    return handle


def _build_user_message(record, now=None):
    """نص رسالة المستخدم الواحدة: الرقم الدائم، الهوية، العدادات، الحالة."""
    now = now or now_damascus()
    online = is_online(record, now)
    status = "🟢 يستخدم البوت الآن" if online else "⚪ غير نشط"

    last_epoch = record.get("last_active_epoch")
    if last_epoch:
        last_dt = datetime.fromtimestamp(float(last_epoch), DAMASCUS_TZ)
        last_text = format_last_active(last_dt)
        if not online:
            last_text += f" (منذ {_humanize_elapsed(now.timestamp() - float(last_epoch))})"
    else:
        last_text = "-"

    actions = record.get("actions") or {}
    lines = [
        status,
        f"#{record.get('user_number') or '-'}  |  {_user_display_name(record)}",
        f"id: {record.get('telegram_id')}",
        f"انضم: {record.get('join_date') or '-'}  |  آخر نشاط: {last_text}",
        "",
        "الإجراءات:",
    ]
    for action_key, label in COUNTER_LABELS.items():
        lines.append(f"  {label}: {actions.get(action_key, 0)}")

    subjects_picked = actions.get("select_subject", 0)
    subjects_removed = actions.get("delete_subject", 0)
    lines.append(f"  مواد اختارها: {subjects_picked}  |  حذفها: {subjects_removed}")
    lines.append(f"  إجمالي الإجراءات: {record.get('total_actions', 0)}")

    note = record.get("note")
    if note:
        lines.append("")
        lines.append(f"📝 ملاحظة: {note}")

    return "\n".join(lines)


def _count_actions(count):
    """يُصيغ عدد الإجراءات عربيًا بصيغة الجمع الصحيحة (3 إجراءات، 11 إجراء)."""
    count = _to_int(count, 0)
    if count == 1:
        return "إجراء واحد"
    if count == 2:
        return "إجراءان"
    if 3 <= count <= 10:
        return f"{count} إجراءات"
    return f"{count} إجراء"


def build_dashboard_text(stats=None):
    """نص رسالة اللوحة العامة (تُعدَّل في مكانها كل فترة)."""
    stats = stats or get_stats()
    now = stats["now"]
    lines = [
        "📊 لوحة webseeker",
        f"{now.strftime('%Y-%m-%d  %I:%M:%S %p')}",
        "",
        f"إجمالي المستخدمين: {stats['total_users']}",
        f"جدد اليوم: {stats['new_today']}  |  جدد هذا الأسبوع: {stats['new_this_week']}",
        f"🟢 متصلون الآن: {len(stats['online_ids'])}",
    ]

    if stats["online_records"]:
        lines.append("")
        lines.append("المتصلون الآن:")
        for uid in stats["online_ids"]:
            record = stats["online_records"].get(uid)
            if not record:
                lines.append(f"  • {uid}")
                continue
            lines.append(
                f"  • #{record.get('user_number') or '-'} "
                f"{_user_display_name(record)}"
            )

    totals = stats["action_totals"]
    lines.append("")
    lines.append(f"إجمالي الإجراءات: {stats['total_actions']}")
    for action_key, label in COUNTER_LABELS.items():
        lines.append(f"  {label}: {totals.get(action_key, 0)}")
    lines.append(f"  مواد مختارة: {totals.get('select_subject', 0)}")
    lines.append(f"  تحديثات الجدول: {totals.get('update_schedule', 0)}")

    if stats["top_users"]:
        lines.append("")
        lines.append("أنشط 5 مستخدمين:")
        for entry in stats["top_users"]:
            record = entry.get("record") or {}
            name = _user_display_name(record) if record else entry["user_id"]
            number = record.get("user_number")
            prefix = f"#{number} " if number else ""
            lines.append(f"  • {prefix}{name} — {_count_actions(entry['actions'])}")

    if stats["peak_hour"] is not None:
        lines.append("")
        lines.append(
            f"ذروة الاستخدام: الساعة {stats['peak_hour']:02d}:00 "
            f"({_count_actions(stats['peak_hour_count'])})"
        )
    if stats["peak_day"] is not None and 0 <= stats["peak_day"] < 7:
        lines.append(
            f"أكثر يوم: {WEEKDAY_NAMES[stats['peak_day']]} "
            f"({_count_actions(stats['peak_day_count'])})"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# إرسال/تعديل الرسائل في تيليغرام
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


def _send_message(text):
    result = _telegram_api("sendMessage", {"chat_id": NOTIFIER_CHAT_ID, "text": text})
    return result.get("message_id") if result else None


def _edit_message(message_id, text):
    return _telegram_api("editMessageText", {
        "chat_id": NOTIFIER_CHAT_ID,
        "message_id": message_id,
        "text": text,
    }) is not None


def _delete_message(message_id):
    # عدم التحقق من نجاح الحذف عمدًا: إن كانت الرسالة محذوفة مسبقًا أو قديمة
    # جدًا، فشل الحذف لا يهم — المهم إرسال الرسالة الجديدة بعده.
    _telegram_api("deleteMessage", {
        "chat_id": NOTIFIER_CHAT_ID,
        "message_id": message_id,
    })


def _push_user_message(user_id):
    """يحذف رسالة المستخدم القديمة ويرسل رسالة جديدة بالمحتوى المحدَّث.

    نستخدم حذف+إرسال عمدًا بدل editMessageText: التعديل يُحدّث المحتوى فقط،
    لكنه لا "يرفع" الرسالة لآخر المحادثة أبدًا — فيبقى نشاط مستخدم قديم
    مخفيًا في أعلى الشات إذا تراكمت رسائل غيره بعده. الحذف ثم الإرسال يضمن
    ظهور الرسالة دائمًا في آخر المحادثة عند أي نشاط جديد.
    """
    if not _notify_enabled:
        return
    with _user_locks_guard:
        snapshot = _pending_records.pop(user_id, None)

    with _get_user_lock(user_id):
        # عند توفّر Redis نعيد القراءة منه لأن حدثًا آخر قد يكون عدّل السجل
        # بعد جدولة هذا الدفع؛ وبدونه نكتفي بأحدث لقطة مرّت عبر الذاكرة.
        record = load_user_record(user_id) if _storage_enabled else snapshot
        if record is None:
            return
        old_message_id = record.get("message_id")
        text = _build_user_message(record)
        if old_message_id:
            _delete_message(old_message_id)
        new_id = _send_message(text)
        if new_id:
            record["message_id"] = new_id
            if _storage_enabled:
                save_user_record(user_id, record)
            else:
                with _user_locks_guard:
                    _pending_records[user_id] = record


def _schedule_push(user_id, record=None):
    """يؤجّل دفع رسالة المستخدم: كل حدث جديد يلغي المؤقت السابق ويعيد
    جدولته، فلا تُرسَل إلا رسالة واحدة بعد توقف المستخدم عن الضغط بثوانٍ.

    هذا ما يمنع تكرار الرسائل الذي كان يحدث عند الضغط السريع، ويخفّض عدد
    طلبات تيليغرام بشكل كبير، دون أي تأخير في حفظ البيانات نفسها (السجل
    يُحفَظ في Redis فورًا في كل حدث، والتأجيل يطال العرض فقط).
    """
    if not _notify_enabled:
        return
    with _user_locks_guard:
        if record is not None:
            _pending_records[user_id] = record
        timer = _push_timers.get(user_id)
        if timer is not None:
            timer.cancel()
        timer = threading.Timer(PUSH_DEBOUNCE_SECONDS, _push_user_message, args=(user_id,))
        timer.daemon = True
        _push_timers[user_id] = timer
        timer.start()


# ---------------------------------------------------------------------------
# اللوحة الدورية
# ---------------------------------------------------------------------------

def refresh_dashboard(force=False):
    """يحدّث رسالة اللوحة العامة في مكانها.

    تُعدَّل (لا تُحذف وتُعاد) لأنها مرجع ثابت يجب أن يبقى في موقعه، بعكس
    رسائل المستخدمين التي نريدها أن تطفو. ولا تُرسَل أي طلب إلى تيليغرام
    إن لم يتغير المحتوى فعليًا، تفاديًا لخطأ "message is not modified"
    ولتوفير الطلبات عندما يكون البوت خاملًا.

    تُستدعى من مهمة دورية في bot.py. على خطة Render المجانية تتوقف هذه
    المهمة عندما تنام الخدمة لعدم وجود طلبات، وتُستأنف مع أول طلب — وهذا
    سلوك مقبول لأن اللوحة ليست بيانات، بل عرضًا لحالة Redis.
    """
    if not _notify_enabled or not _storage_enabled:
        return
    stats = get_stats()
    text = build_dashboard_text(stats)
    # بصمة مستقرة: لا نستخدم hash() المدمجة لأن قيمتها تتغير بين تشغيلات
    # بايثون، فكانت ستجعل اللوحة تُعدَّل مرة زائدة بعد كل إعادة إقلاع.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    if not force and digest == _redis_call("GET", DASHBOARD_HASH_KEY):
        return

    message_id = _to_int(_redis_call("GET", DASHBOARD_MESSAGE_KEY))
    if message_id and _edit_message(message_id, text):
        _redis_call("SET", DASHBOARD_HASH_KEY, digest)
        return

    # لا توجد لوحة بعد، أو أن الرسالة القديمة حُذفت/أصبحت غير قابلة للتعديل.
    new_id = _send_message(text)
    if new_id:
        _redis_pipeline([
            ["SET", DASHBOARD_MESSAGE_KEY, new_id],
            ["SET", DASHBOARD_HASH_KEY, digest],
        ])


# ---------------------------------------------------------------------------
# الواجهة العامة المستخدمة من bot.py
# ---------------------------------------------------------------------------

def touch(user_id, username=None, full_name=None):
    """نبضة حضور: تؤكّد وجود السجل (وتنشئه إن كان أول ظهور) وتحدّث آخر نشاط.

    تُستدعى عند كل تفاعل مع البوت. لا تزيد أي عدّاد ولا تدفع رسالة إلى
    تيليغرام — وظيفتها إبقاء "متصل الآن" و"آخر نشاط" دقيقَين. الاستثناء
    الوحيد: مستخدم يظهر لأول مرة، فتُرسل رسالته الأولى.

    idempotent بالكامل: هذا هو الإصلاح الجوهري لمشكلة كانت تمحو البيانات.
    في النسخة السابقة كان bot.py يعتمد على مجموعة `seen_users` في الذاكرة
    ليقرر من هو الجديد، ويستدعي register_new_user الذي ينشئ سجلًا جديدًا بلا
    شروط. النتيجة: بعد كل إعادة تشغيل على Render، يُعامل كل المستخدمين
    القدامى كجدد وتُمحى أرقامهم وعداداتهم. الآن القرار يُتخذ من Redis نفسه،
    فإعادة التشغيل لا تغيّر شيئًا.

    الخنق الزمني (كل PRESENCE_THROTTLE_SECONDS لكل مستخدم) يجعل هذه النبضة
    رخيصة رغم أنها تُستدعى مع كل ضغطة زر: نافذة "الاتصال الآن" ثلاث دقائق،
    فدقّة بمقياس ثوانٍ لا معنى لها.
    """
    now = now_damascus()
    epoch = now.timestamp()
    if _presence_throttled(user_id, epoch):
        return

    with _get_user_lock(user_id):
        record = load_user_record(user_id)
        is_new = record is None
        if is_new:
            record = _new_record(user_id, username, full_name, now)
        else:
            record = _migrate_record(user_id, record)
        _touch_record(record, username, full_name, now)
        save_user_record(user_id, record)

    _record_presence(user_id, epoch)

    if is_new:
        logger.info("مستخدم جديد #%s: %s (%s)",
                    record.get("user_number"), full_name or username, user_id)
        _schedule_push(user_id, record)


def log_event(user_id, username, event_type, value="", full_name=None):
    """يسجّل حدثًا حقيقيًا (اختيار مادة، توليد جدول، ...) ويحدّث رسالة المستخدم.

    يُنشئ السجل تلقائيًا إن لم يكن موجودًا حتى لا يُفقد أي حدث. العدّادات
    تُحفَظ في Redis فورًا، أما الرسالة فتُدفع عبر المرجئ (انظر _schedule_push)
    فتصل مرة واحدة مهما تتابعت الضغطات.

    كل حدث محاط بقفل خاص بالمستخدم لأن bot.py يُطلق الأحداث كمهام خلفية
    متوازية؛ بدون القفل قد تُفقد زيادة عدّاد أو تُرسَل رسالتان لحدثين
    متزامنين.
    """
    now = now_damascus()
    epoch = now.timestamp()

    with _get_user_lock(user_id):
        record = load_user_record(user_id)
        is_new = record is None
        if is_new:
            record = _new_record(user_id, username, full_name, now)
        else:
            record = _migrate_record(user_id, record)

        _touch_record(record, username, full_name, now)
        actions = record.setdefault("actions", {})
        actions[event_type] = actions.get(event_type, 0) + 1
        record["total_actions"] = sum(actions.values())

        events = record.setdefault("events", [])
        events.append({
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "type": event_type,
            "value": value,
        })
        # السجل التفصيلي لم يعد يظهر في رسالة المستخدم (بقيت العدادات فقط
        # لتكون الرسالة قصيرة وقابلة للقراءة)، لكنه يُحفَظ هنا للمراجعة
        # اللاحقة عبر أوامر الإدارة في شات الإشعارات.
        record["events"] = events[-MAX_EVENTS_KEPT:]

        save_user_record(user_id, record)
        _log_activity_indexes(user_id, event_type, epoch, now)

    _record_presence(user_id, epoch)
    # يُضبط خنق الحضور هنا أيضًا، وإلا أعادت نبضة touch التالية كتابة نفس
    # آخر نشاط مرتين خلال ثوانٍ.
    with _user_locks_guard:
        _presence_last_write[user_id] = epoch
    _schedule_push(user_id, record)
    return is_new


def notify_update_result(success: bool, detail: str = "") -> None:
    """إشعار عام (غير مرتبط بمستخدم محدد) بنتيجة تحديث أوقات الجدول.
    يُرسَل كرسالة جديدة منفصلة كل مرة، لأنه ليس جزءًا من متابعة مستخدم."""
    if success:
        text = "تحديث أوقات الجدول: تم بنجاح."
    else:
        text = "تحديث أوقات الجدول: فشل."
        if detail:
            text += f"\nالتفاصيل: {detail}"
    _send_message(text)


# ---------------------------------------------------------------------------
# أدوات الإدارة والمراجعة (تستخدمها notifier_admin)
# ---------------------------------------------------------------------------

def _scan_user_keys(batch_size=300):
    """يمسح كل مفاتيح المستخدمين في Redis تدريجيًا عبر SCAN.

    نستخدم SCAN بدل KEYS لأن KEYS يحجب خادم Redis كاملًا أثناء المسح، وهو
    أمر غير مقبول على قاعدة مشتركة حتى لو كان عدد المستخدمين صغيرًا الآن.
    """
    keys = []
    cursor = "0"
    while True:
        result = _redis_call("SCAN", cursor, "MATCH", "iust_user:*", "COUNT", batch_size)
        if not isinstance(result, list) or len(result) < 2:
            break
        cursor = str(result[0])
        keys.extend(result[1] or [])
        if cursor == "0":
            break
    return keys


def iter_all_records(batch_size=100):
    """يُعيد كل سجلات المستخدمين (مولّد) لقراءتها دفعةً دفعة."""
    keys = _scan_user_keys()
    for start in range(0, len(keys), batch_size):
        chunk = keys[start:start + batch_size]
        results = _redis_pipeline([["GET", key] for key in chunk]) or []
        for raw in results:
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except (TypeError, ValueError):
                continue


def find_user(identifier):
    """يبحث عن مستخدم بمعرّف تيليغرام أو برقمه الدائم أو باسم المستخدم."""
    identifier = str(identifier).strip().lstrip("@")
    as_id = _to_int(identifier)
    if as_id is not None:
        record = load_user_record(as_id)
        if record:
            return record

    for record in iter_all_records():
        if as_id is not None and _to_int(record.get("user_number")) == as_id:
            return record
        if identifier and identifier.lower() == str(record.get("username", "")).lower():
            return record
    return None


def set_note(user_id, note):
    """يحفظ ملاحظة مراجعة على سجل مستخدم (تظهر في رسالته وفي /user)."""
    with _get_user_lock(user_id):
        record = load_user_record(user_id)
        if record is None:
            return False
        record["note"] = note
        save_user_record(user_id, record)
    _schedule_push(user_id)
    return True


def list_recent_users(limit=20):
    """آخر المستخدمين انضمامًا (من الفهرس، دون مسح كل المفاتيح)."""
    raw = _redis_call("ZREVRANGE", USERS_INDEX_KEY, 0, max(0, limit - 1)) or []
    user_ids = [_to_int(item) for item in raw if _to_int(item) is not None]
    records = _load_records(user_ids)
    return [records[uid] for uid in user_ids if uid in records]


def export_json():
    """تفريغ كل البيانات كملف JSON (نسخة احتياطية ومراجعة خارجية)."""
    payload = {
        "exported_at": now_damascus().isoformat(),
        "schema_version": SCHEMA_VERSION,
        "stats": _stats_for_export(),
        "users": sorted(
            iter_all_records(),
            key=lambda r: _to_int(r.get("user_number"), 0),
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _stats_for_export():
    stats = get_stats()
    return {
        "total_users": stats["total_users"],
        "new_today": stats["new_today"],
        "new_this_week": stats["new_this_week"],
        "action_totals": stats["action_totals"],
        "total_actions": stats["total_actions"],
        "peak_hour": stats["peak_hour"],
        "peak_day": stats["peak_day"],
    }


EXPORT_COLUMNS = [
    ("user_number", "الرقم الدائم"),
    ("telegram_id", "المعرّف"),
    ("username", "اسم المستخدم"),
    ("full_name", "الاسم الكامل"),
    ("join_date", "تاريخ الانضمام"),
    ("last_active", "آخر نشاط"),
    ("selected_schedules", "جدول مواد مختارة"),
    ("all_times", "جدول كل المواد"),
    ("optimized", "جدول مثالي"),
    ("total_actions", "إجمالي الإجراءات"),
    ("note", "ملاحظة"),
]


def export_csv():
    """تفريغ كل البيانات كملف CSV يفتح مباشرة في Excel."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in EXPORT_COLUMNS])

    records = sorted(iter_all_records(), key=lambda r: _to_int(r.get("user_number"), 0))
    for record in records:
        actions = record.get("actions") or {}
        last_epoch = record.get("last_active_epoch")
        last_text = (
            datetime.fromtimestamp(float(last_epoch), DAMASCUS_TZ).strftime("%Y-%m-%d %H:%M:%S")
            if last_epoch else ""
        )
        row = {
            "last_active": last_text,
            "selected_schedules": actions.get(ACTION_SELECTED_SCHEDULE, 0),
            "all_times": actions.get(ACTION_ALL_TIMES, 0),
            "optimized": actions.get(ACTION_OPTIMIZED, 0),
        }
        writer.writerow([
            row.get(key, record.get(key, "")) for key, _ in EXPORT_COLUMNS
        ])

    # BOM يجعل Excel يقرأ العربية بترميز صحيح بدل رموز مشوّهة.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def rebuild_indexes():
    """يعيد بناء كل الفهارس من سجلات المستخدمين نفسها.

    أداة صيانة للمالك: إن فُقد فهرس (أو بقيت سجلات قديمة من قبل وجود
    الفهارس)، تُرمَّم الإحصائيات من المصدر الوحيد الموثوق — السجلات نفسها —
    دون المساس بالأرقام الدائمة المخزَّنة داخلها. يُعاد بناء الفهرس من
    الصفر، بينما عدّادات الإجراءات العامة تُرمَّم بمسح سجل الأحداث المحفوظ.
    """
    records = list(iter_all_records())
    commands = [["DEL", USERS_INDEX_KEY], ["DEL", ACTIVITY_INDEX_KEY],
                ["DEL", PRESENCE_KEY], ["DEL", ACTION_TOTALS_KEY],
                ["DEL", HOUR_HISTOGRAM_KEY], ["DEL", DAY_HISTOGRAM_KEY]]

    now_ts = now_damascus().timestamp()
    action_totals = {}
    hour_totals = {}
    day_totals = {}
    highest_number = 0

    for record in records:
        user_id = _to_int(record.get("telegram_id"))
        if user_id is None:
            continue
        record = _migrate_record(user_id, record)
        highest_number = max(highest_number, _to_int(record.get("user_number"), 0))
        commands.append(["ZADD", USERS_INDEX_KEY, record.get("join_epoch") or now_ts, str(user_id)])
        total = _to_int(record.get("total_actions"), 0)
        if total:
            commands.append(["ZADD", ACTIVITY_INDEX_KEY, total, str(user_id)])
        if is_online(record):
            commands.append(["ZADD", PRESENCE_KEY, record.get("last_active_epoch"), str(user_id)])

        for action_type, count in (record.get("actions") or {}).items():
            action_totals[action_type] = action_totals.get(action_type, 0) + count

        for event in record.get("events") or []:
            stamp = event.get("time") or event.get("t")
            if not stamp:
                continue
            try:
                moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=DAMASCUS_TZ)
            except ValueError:
                continue
            hour_totals[moment.hour] = hour_totals.get(moment.hour, 0) + 1
            day_totals[moment.weekday()] = day_totals.get(moment.weekday(), 0) + 1

    for action_type, count in action_totals.items():
        commands.append(["HSET", ACTION_TOTALS_KEY, action_type, count])
    for hour, count in hour_totals.items():
        commands.append(["HSET", HOUR_HISTOGRAM_KEY, str(hour), count])
    for day, count in day_totals.items():
        commands.append(["HSET", DAY_HISTOGRAM_KEY, str(day), count])

    for start in range(0, len(commands), 200):
        _redis_pipeline(commands[start:start + 200])

    # لا يُخفَّض العدّاد أبدًا: تخفيضه يعني أن مستخدمًا جديدًا قد يحصل لاحقًا
    # على رقم مستخدم قديم، وهذا يناقض كون الرقم دائمًا. نرفعه فقط إن كان
    # أقلّ من أعلى رقم موجود فعليًا في السجلات.
    current = _to_int(_redis_call("GET", TOTAL_USERS_COUNTER_KEY), 0) or 0
    if highest_number > current:
        _redis_call("SET", TOTAL_USERS_COUNTER_KEY, highest_number)
    return len(records)
