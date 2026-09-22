#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notifier.py  (الإصدار 2 -- إعادة بناء كاملة)
----------------------------------------------
نظام مراقبة لمالك البوت: رسالة واحدة ثابتة لكل مستخدم في شات الإشعارات
(تُعدَّل في مكانها ولا تُكرَّر أبدًا)، بالإضافة إلى رسالة "لوحة إحصائيات"
عامة واحدة (تُثبَّت أعلى الشات) تُعدَّل بنفس الطريقة.

ماذا تعرض رسالة كل مستخدم؟
    1) رقم تسلسلي دائم (#1, #2, ...) لا يتغيّر أبدًا ولا يُعاد استخدامه.
    2) الاسم واسم المستخدم (@username) والـ id.
    3) عدد مرات: (عرض جدول مواد مختارة) / (جدول أوقات كل المواد) /
       (تركيب جدول مثالي).
    4) إشارة 🟢 إذا كان يستخدم البوت الآن (نشاط خلال آخر 3 دقائق)، أو ⚪.
    + تاريخ الانضمام، آخر نشاط، عدد التفاعلات والجلسات، آخر إجراء.

لماذا تعديل الرسالة بدل حذفها وإعادة إرسالها؟
    الطريقة القديمة (حذف + إرسال) كانت تُنتج رسائل مكرّرة عندما يفشل
    الحذف (تيليغرام لا يحذف الرسائل الأقدم من 48 ساعة، والتحديثات
    المتزامنة، وتداخل نسختين من الخدمة أثناء النشر على Render). الآن
    الرسالة تُعدَّل في مكانها دائمًا. تُرسَل رسالة جديدة فقط إذا لم تكن
    هناك رسالة أصلًا، أو إذا حذفتَها أنت يدويًا من الشات.

كيف يتجنّب النظام حدود تيليغرام وحدود Upstash المجانية؟
    - كل الأحداث تُسجَّل في الذاكرة فورًا (بلا أي طلب شبكي في مسار
      معالجة أزرار البوت، فلا يتأخر البوت على المستخدمين أبدًا).
    - خيط خلفي واحد يجمع التغييرات: يعدّل رسالة واحدة تقريبًا كل ثانية
      (حد تيليغرام)، وأكثر من ضغطة لنفس المستخدم تُدمَج في تعديل واحد.
    - الحفظ في Upstash يتم دفعة واحدة كل 30 ثانية (وفورًا عند انضمام
      مستخدم جديد)، فيبقى الاستهلاك بعيدًا جدًا عن حد الخطة المجانية.

    ملاحظة إصلاح (مهمة): سابقًا كانت لوحة الإحصائيات العامة تُعطى الأولوية
    المطلقة على رسائل المستخدمين الفردية في كل دورة (if dashboard_due:
    ... elif candidates: ...). إن فشل تعديل رسالة اللوحة تحديدًا بشكل
    *دائم* (وليس عابرًا) -- مثلًا بسبب حظر بوت الإشعارات، أو تغيّر
    NOTIFIER_CHAT_ID، أو أي خطأ آخر غير "الرسالة غير موجودة" -- كان
    النظام يدخل حلقة: يحاول اللوحة، يفشل، يوقف نفسه 10 ثوانٍ، ثم يحاول
    اللوحة من جديد أولًا لأنها لا تزال "مستحقة"، وهكذا للأبد. النتيجة:
    لا تُعدَّل أي رسالة مستخدم أبدًا (لا عدد الجداول ولا آخر نشاط)، رغم
    أن العدّادات نفسها كانت تتحدّث بشكل صحيح تمامًا في الذاكرة وفي
    Upstash. الإصلاح: (1) إعطاء الأولوية دائمًا لتحديث رسائل المستخدمين
    على اللوحة، و(2) تباعد تصاعدي (exponential backoff) لإعادة محاولة
    اللوحة عند الفشل بدل إعادة المحاولة كل 10 ثوانٍ للأبد.

أين تُحفَظ بيانات المستخدمين؟
    في Upstash Redis (دائمة، لا تضيع بإعادة نشر Render):
      iust:users  -> Hash: كل مستخدم سجل JSON كامل
      iust:global -> إحصائيات عامة (عدّاد الأرقام، أكثر المواد، الساعات...)
    وللمراجعة والإكمال لاحقًا: الأمر /export (للمالك فقط) يرسل لك نسخة
    JSON كاملة + ملف CSV يفتح في Excel، ويمكنك إعادة إرسال ملف JSON للبوت
    لاستيراده ودمجه (يُكمل الناقص ولا يمسح الموجود). راجع bot.py.

الإعداد (متغيرات البيئة على Render، بدون تغيير عن السابق):
    NOTIFIER_BOT_TOKEN, NOTIFIER_CHAT_ID,
    UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN

إن لم تُضبط متغيرات تيليغرام: يستمر التخزين بدون إرسال إشعارات. إن لم
تُضبط متغيرات Upstash: يعمل كل شيء في الذاكرة فقط (يضيع عند إعادة التشغيل).
بوت الجدول الأساسي لا يعتمد على هذه الوحدة إطلاقًا ولا يتعطل بسببها.
"""

import os
import io
import csv
import copy
import json
import html
import time
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
    logger.warning("إشعارات تيليغرام الخاصة بالمالك غير مُفعَّلة "
                   "(لم يتم ضبط NOTIFIER_BOT_TOKEN أو NOTIFIER_CHAT_ID).")
if not _storage_enabled:
    logger.warning("التخزين الدائم (Upstash Redis) غير مُفعَّل؛ ستُحفَظ البيانات في الذاكرة فقط.")

DAMASCUS_TZ = timezone(timedelta(hours=3))

# ---------------------------------------------------------------------------
# الإعدادات
# ---------------------------------------------------------------------------
ONLINE_WINDOW_SECONDS = 180      # آخر نشاط خلال 3 دقائق = "يستخدم البوت الآن"
SWEEP_INTERVAL_SECONDS = 15      # كل كم ثانية نتحقق ممن انتهت جلستهم
PERSIST_INTERVAL_SECONDS = 30    # الحفظ الدوري في Upstash
TICK_SECONDS = 1.1               # خطوة الخيط الخلفي (≈ تعديل واحد/ثانية)
MIN_USER_EDIT_INTERVAL = 5       # أقل فاصل بين تعديلين لرسالة نفس المستخدم
MIN_DASHBOARD_INTERVAL = 20      # أقل فاصل بين تعديلين للوحة الإحصائيات
LOAD_RETRY_INTERVAL = 30

# سقف أقصى لفاصل إعادة محاولة اللوحة عند الفشل المتكرر (تباعد تصاعدي
# بدل إعادة المحاولة كل MIN_DASHBOARD_INTERVAL ثانية للأبد، انظر الشرح
# في أعلى الملف).
DASHBOARD_MAX_BACKOFF_SECONDS = 30 * 60

USERS_HASH = "iust:users"
GLOBAL_KEY = "iust:global"
MIGRATED_KEY = "iust:migrated_v2"
OLD_USER_PREFIX = "iust_user:"   # مفاتيح النظام القديم (تُرحَّل تلقائيًا مرة واحدة)

FEATURES = ("selected_schedule", "all_times", "optimal")

# ---------------------------------------------------------------------------
# الحالة (في الذاكرة، محمية بقفل واحد)
# ---------------------------------------------------------------------------
_lock = threading.RLock()
_users = {}            # telegram_id -> record
_global = {}           # إحصائيات عامة
_dirty_render = {}     # telegram_id -> (وقت أول تغيير، مهم؟)
_last_render = {}      # telegram_id -> وقت آخر تعديل ناجح
_dirty_persist = set()
_global_dirty = False
_urgent_persist = False
_dashboard_dirty = False
_dashboard_last = 0.0
_dashboard_fail_streak = 0   # عدد مرات فشل تعديل اللوحة المتتالية
_alerts = []
_tg_blocked_until = 0.0
_last_sweep = 0.0
_last_persist = 0.0
_last_load_try = 0.0
_ready = not _storage_enabled   # مع Upstash: لا نبدأ التسجيل قبل تحميل البيانات القديمة
_worker = None
_stop_event = threading.Event()


class _RedisError(Exception):
    pass


# ---------------------------------------------------------------------------
# أدوات عامة
# ---------------------------------------------------------------------------

def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=False)


def _fmt_dt(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, DAMASCUS_TZ).strftime("%Y-%m-%d %H:%M")


def _fmt_date(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, DAMASCUS_TZ).strftime("%Y-%m-%d")


def _default_global():
    return {
        "next_user_no": 1,
        "course_counts": {},
        "hour_sessions": [0] * 24,
        "update": {"ok": 0, "fail": 0, "last_ts": 0.0, "last_ok": None, "last_detail": ""},
        "dashboard_message_id": None,
    }


def _normalize_global(g):
    base = _default_global()
    if not isinstance(g, dict):
        return base
    base["next_user_no"] = max(1, int(g.get("next_user_no") or 1))
    counts = g.get("course_counts") or {}
    base["course_counts"] = {str(k): int(v) for k, v in counts.items()} if isinstance(counts, dict) else {}
    hours = g.get("hour_sessions") or []
    if isinstance(hours, list) and len(hours) == 24:
        base["hour_sessions"] = [int(x or 0) for x in hours]
    upd = g.get("update")
    if isinstance(upd, dict):
        base["update"].update({k: upd[k] for k in base["update"] if k in upd})
    base["dashboard_message_id"] = g.get("dashboard_message_id")
    return base


_global.update(_default_global())

_RECORD_DEFAULTS = {
    "user_no": None, "username": None, "full_name": None,
    "join_ts": 0.0, "last_active_ts": 0.0,
    "clicks": 0, "sessions": 0,
    "last_action": "", "last_action_ts": 0.0,
    "message_id": None, "msg_online": False,
}


def _normalize_record(rec):
    """يكمّل الحقول الناقصة (توافق مع النسخ القديمة ومع الاستيراد)."""
    for key, default in _RECORD_DEFAULTS.items():
        rec.setdefault(key, default)
    rec["telegram_id"] = int(rec["telegram_id"])
    counts = rec.get("counts") if isinstance(rec.get("counts"), dict) else {}
    rec["counts"] = {f: int(counts.get(f, 0) or 0) for f in FEATURES}
    for key in ("clicks", "sessions"):
        rec[key] = int(rec[key] or 0)
    for key in ("join_ts", "last_active_ts", "last_action_ts"):
        rec[key] = float(rec[key] or 0.0)
    return rec


def _allocate_user_no():
    """رقم جديد لا يُستخدم مرتين أبدًا (يُستدعى والقفل ممسوك)."""
    global _global_dirty, _urgent_persist
    number = _global["next_user_no"]
    _global["next_user_no"] = number + 1
    _global_dirty = True
    _urgent_persist = True
    return number


def _mark_dirty(uid, important=False, urgent=False):
    """يُستدعى والقفل ممسوك."""
    global _dashboard_dirty, _urgent_persist
    first_ts, was_important = _dirty_render.get(uid, (time.time(), False))
    _dirty_render[uid] = (first_ts, was_important or important)
    _dirty_persist.add(uid)
    _dashboard_dirty = True
    if urgent:
        _urgent_persist = True


# ---------------------------------------------------------------------------
# Upstash Redis (REST)
# ---------------------------------------------------------------------------

def _redis_raw(*parts, timeout=15):
    try:
        resp = requests.post(UPSTASH_URL, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                             json=list(parts), timeout=timeout)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise _RedisError(type(exc).__name__) from exc
    if resp.status_code != 200 or not isinstance(data, dict) or "error" in data:
        raise _RedisError(f"HTTP {resp.status_code}: {str(data)[:200]}")
    return data.get("result")


def _redis_pipeline(commands, timeout=30):
    try:
        resp = requests.post(UPSTASH_URL + "/pipeline", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                             json=commands, timeout=timeout)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise _RedisError(type(exc).__name__) from exc
    if resp.status_code != 200 or not isinstance(data, list):
        raise _RedisError(f"HTTP {resp.status_code}: {str(data)[:200]}")
    results = []
    for item in data:
        if isinstance(item, dict) and "error" in item:
            raise _RedisError(str(item["error"])[:200])
        results.append(item.get("result") if isinstance(item, dict) else item)
    return results


def _hash_items(raw):
    if not raw:
        return []
    if isinstance(raw, dict):
        return list(raw.items())
    return list(zip(raw[0::2], raw[1::2]))


# ---------------------------------------------------------------------------
# التحميل من Upstash + ترحيل بيانات النظام القديم مرة واحدة
# ---------------------------------------------------------------------------

def _read_old_records():
    """يقرأ سجلات النظام القديم (iust_user:<id>) كي لا تتكرر رسائلها."""
    keys, cursor = [], "0"
    while True:
        res = _redis_raw("SCAN", cursor, "MATCH", OLD_USER_PREFIX + "*", "COUNT", "200")
        cursor, batch = str(res[0]), res[1]
        keys.extend(batch)
        if cursor == "0":
            break
    old = []
    for i in range(0, len(keys), 100):
        chunk = keys[i:i + 100]
        for raw in _redis_raw("MGET", *chunk) or []:
            if not raw:
                continue
            try:
                item = json.loads(raw)
                item["telegram_id"] = int(item["telegram_id"])
                old.append(item)
            except (TypeError, ValueError, KeyError):
                continue
    return old


def _merge_old_records(old_list):
    """يحوّل السجلات القديمة لسجلات جديدة (القفل ممسوك). يعيد عدد المضاف."""
    added = 0
    old_list = sorted(old_list, key=lambda r: (str(r.get("join_date", "")), r["telegram_id"]))
    for old in old_list:
        uid = old["telegram_id"]
        if uid in _users:
            continue
        try:
            join_ts = datetime.strptime(old.get("join_date", ""), "%Y-%m-%d").replace(tzinfo=DAMASCUS_TZ).timestamp()
        except ValueError:
            join_ts = time.time()
        try:
            last_ts = datetime.fromisoformat(old["last_active_iso"]).timestamp()
        except (KeyError, TypeError, ValueError):
            last_ts = join_ts
        rec = _normalize_record({
            "telegram_id": uid, "user_no": _allocate_user_no(),
            "username": old.get("username"), "join_ts": join_ts, "last_active_ts": last_ts,
            "counts": {"selected_schedule": old.get("schedules_created", 0)},
            "message_id": old.get("message_id"),
        })
        _users[uid] = rec
        _dirty_persist.add(uid)
        added += 1
    return added


def _try_load():
    global _ready, _last_load_try
    _last_load_try = time.time()
    try:
        raw_users = _redis_raw("HGETALL", USERS_HASH)
        raw_global = _redis_raw("GET", GLOBAL_KEY)
        migrated = _redis_raw("GET", MIGRATED_KEY)
        old_records = [] if migrated else _read_old_records()
    except _RedisError as exc:
        logger.error("تعذّر تحميل بيانات المستخدمين من Upstash: %s", exc)
        return False

    users = {}
    for field, value in _hash_items(raw_users):
        try:
            rec = json.loads(value)
            rec["telegram_id"] = int(field)
            users[int(field)] = _normalize_record(rec)
        except (TypeError, ValueError):
            logger.warning("سجل مستخدم تالف في Upstash (الحقل %s) تم تجاهله.", field)
    try:
        glob = _normalize_global(json.loads(raw_global)) if raw_global else _default_global()
    except (TypeError, ValueError):
        glob = _default_global()

    with _lock:
        _users.clear()
        _users.update(users)
        _global.clear()
        _global.update(glob)
        migrated_count = _merge_old_records(old_records)
        top = max([r["user_no"] or 0 for r in _users.values()] or [0])
        _global["next_user_no"] = max(_global["next_user_no"], top + 1)
        # أي مستخدم بلا رسالة (أو مُرحَّل من النظام القديم) يُجدَّد شكل رسالته تدريجيًا
        for uid, rec in _users.items():
            if migrated_count or not rec.get("message_id"):
                _dirty_render.setdefault(uid, (time.time(), False))
        for rec in _users.values():
            if rec["user_no"] is None:
                rec["user_no"] = _allocate_user_no()
                _dirty_persist.add(rec["telegram_id"])
        global _dashboard_dirty, _global_dirty
        _dashboard_dirty = True
        if migrated_count:
            _global_dirty = True
        _ready = True

    logger.info("تم تحميل %s مستخدمًا من Upstash%s.", len(users),
                f" (وتم ترحيل {migrated_count} مستخدم من النظام القديم)" if migrated_count else "")
    if not migrated:
        if _persist_pending():
            try:
                _redis_raw("SET", MIGRATED_KEY, "1")
            except _RedisError:
                logger.warning("تعذّر ضبط علامة اكتمال الترحيل؛ ستُعاد المحاولة بأمان عند التشغيل القادم.")
    return True


# ---------------------------------------------------------------------------
# الحفظ الدوري (دفعة واحدة)
# ---------------------------------------------------------------------------

def _persist_pending():
    global _urgent_persist, _last_persist, _global_dirty
    with _lock:
        ids = [uid for uid in _dirty_persist if uid in _users]
        _dirty_persist.clear()
        entries = [(str(uid), json.dumps(_users[uid], ensure_ascii=False)) for uid in ids]
        glob_json = json.dumps(_global, ensure_ascii=False) if _global_dirty else None
        _global_dirty = False
        _urgent_persist = False
    _last_persist = time.time()

    if not _storage_enabled or (not entries and glob_json is None):
        return True

    commands = []
    for i in range(0, len(entries), 50):  # أمر HSET واحد لكل 50 مستخدمًا
        commands.append(["HSET", USERS_HASH] + [x for pair in entries[i:i + 50] for x in pair])
    if glob_json is not None:
        commands.append(["SET", GLOBAL_KEY, glob_json])
    try:
        _redis_pipeline(commands)
        return True
    except _RedisError as exc:
        logger.error("فشل الحفظ في Upstash (ستُعاد المحاولة): %s", exc)
        with _lock:
            _dirty_persist.update(ids)
            if glob_json is not None:
                _global_dirty = True
        return False


# ---------------------------------------------------------------------------
# بناء نصوص الرسائل
# ---------------------------------------------------------------------------

def _is_online(rec, now):
    return (now - rec["last_active_ts"]) < ONLINE_WINDOW_SECONDS


def _short_name(rec):
    if rec.get("username"):
        return "@" + rec["username"]
    return (rec.get("full_name") or str(rec["telegram_id"]))[:18]


def build_user_text(rec, now=None):
    now = now or time.time()
    online = _is_online(rec, now)
    counts = rec["counts"]
    lines = [
        f"{'🟢' if online else '⚪'} <b>#{rec['user_no']}</b> · {'يستخدم البوت الآن' if online else 'غير متصل'}",
        f"👤 {_esc(rec.get('full_name') or '—')}",
        f"🔗 {_esc('@' + rec['username']) if rec.get('username') else '—'}   🆔 <code>{rec['telegram_id']}</code>",
        f"📅 انضم: {_fmt_date(rec['join_ts'])}",
        f"🕒 آخر نشاط: {_fmt_dt(rec['last_active_ts'])}",
        "──────────────",
        f"📄 جداول مواد مختارة: <b>{counts['selected_schedule']}</b>",
        f"📚 جدول كل المواد: <b>{counts['all_times']}</b>",
        f"🧩 جداول مثالية: <b>{counts['optimal']}</b>",
        "──────────────",
        f"🖱 التفاعلات: {rec['clicks']}  ·  الجلسات: {rec['sessions']}",
        f"⏱ آخر إجراء: {_esc(rec.get('last_action') or '—')}",
    ]
    return "\n".join(lines)


def build_dashboard_text(now=None):
    now = now or time.time()
    with _lock:
        recs = list(_users.values())
        glob = copy.deepcopy(_global)

    total = len(recs)
    today = _fmt_date(now)
    online = sorted((r for r in recs if _is_online(r, now)), key=lambda r: -r["last_active_ts"])
    new_today = sum(1 for r in recs if _fmt_date(r["join_ts"]) == today)
    active_24h = sum(1 for r in recs if now - r["last_active_ts"] < 86400)
    active_7d = sum(1 for r in recs if now - r["last_active_ts"] < 7 * 86400)
    totals = {f: sum(r["counts"][f] for r in recs) for f in FEATURES}
    creators = sum(1 for r in recs if sum(r["counts"].values()) > 0)
    clicks = sum(r["clicks"] for r in recs)
    sessions = sum(r["sessions"] for r in recs)

    lines = [
        "📊 <b>لوحة إحصائيات WebSeeker</b>",
        "══════════════",
        f"👥 إجمالي المستخدمين: <b>{total}</b>",
        f"🟢 يستخدمون البوت الآن: <b>{len(online)}</b>",
    ]
    if online:
        shown = "  ·  ".join(f"#{r['user_no']} {_esc(_short_name(r))}" for r in online[:12])
        if len(online) > 12:
            shown += f"  (+{len(online) - 12})"
        lines.append(shown)
    lines += [
        f"🆕 انضموا اليوم: {new_today}",
        f"📅 نشطون خلال 24 ساعة: {active_24h}  ·  خلال 7 أيام: {active_7d}",
        "──────────────",
        f"📄 جداول مواد مختارة: <b>{totals['selected_schedule']}</b>",
        f"📚 جدول كل المواد: <b>{totals['all_times']}</b>",
        f"🧩 جداول مثالية: <b>{totals['optimal']}</b>",
    ]
    if total:
        lines.append(f"✅ أنشأوا جدولًا واحدًا على الأقل: {creators} ({round(100 * creators / total)}%)")
    lines.append(f"🖱 إجمالي التفاعلات: {clicks}  ·  الجلسات: {sessions}")

    top_courses = sorted(glob["course_counts"].items(), key=lambda kv: -kv[1])[:5]
    if top_courses:
        lines += ["──────────────", "🏆 أكثر المواد اختيارًا:"]
        lines += [f"{i}. {_esc(name)} ({n})" for i, (name, n) in enumerate(top_courses, start=1)]

    hours = glob["hour_sessions"]
    if any(hours):
        peak = sorted(range(24), key=lambda h: -hours[h])[:3]
        lines.append("⏰ ذروة الاستخدام: " + "  ·  ".join(f"{h:02d}:00 ({hours[h]})" for h in peak if hours[h]))

    upd = glob["update"]
    if upd["last_ts"]:
        state = "نجح" if upd["last_ok"] else "فشل"
        lines.append(f"🔄 آخر تحديث للجدول: {state} ({_fmt_dt(upd['last_ts'])})  ·  ناجح {upd['ok']} / فاشل {upd['fail']}")

    lines += ["──────────────", f"🕒 آخر تحديث للوحة: {_fmt_dt(now)}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# تيليغرام
# ---------------------------------------------------------------------------

def _tg(method, payload):
    url = f"https://api.telegram.org/bot{NOTIFIER_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        # لا نطبع الاستثناء نفسه لأنه قد يتضمّن الرابط الذي فيه التوكن
        logger.warning("خطأ شبكة أثناء استدعاء تيليغرام %s (%s)", method, type(exc).__name__)
        return {"ok": False, "description": "network error"}
    if data.get("ok"):
        return {"ok": True, "result": data.get("result")}
    return {"ok": False, "description": str(data.get("description", "")),
            "retry_after": (data.get("parameters") or {}).get("retry_after")}


def _block_telegram(res):
    global _tg_blocked_until
    retry = res.get("retry_after")
    delay = int(retry) + 1 if retry else 10
    _tg_blocked_until = time.time() + delay
    logger.warning("تيليغرام: %s (إيقاف مؤقت %s ثانية)", res.get("description"), delay)


def _upsert_message(message_id, text):
    """يعدّل الرسالة إن وُجدت، وإلا يرسل واحدة جديدة.
    يعيد (نجح؟، رقم الرسالة الحالي أو None، أُنشئت رسالة جديدة؟)"""
    base = {"chat_id": NOTIFIER_CHAT_ID, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    if message_id:
        res = _tg("editMessageText", dict(base, message_id=message_id))
        if res["ok"]:
            return True, message_id, False
        desc = res.get("description", "").lower()
        if "message is not modified" in desc:
            return True, message_id, False
        gone = any(s in desc for s in ("message to edit not found", "message can't be edited",
                                        "message_id_invalid"))
        if not gone:
            _block_telegram(res)          # خطأ عابر: لا نُرسل رسالة جديدة أبدًا (تفاديًا للتكرار)
            return False, message_id, False
        logger.warning("الرسالة القديمة لم تعد موجودة (حُذفت يدويًا غالبًا)؛ ستُنشأ رسالة جديدة.")
    res = _tg("sendMessage", base)
    if res["ok"]:
        return True, res["result"]["message_id"], True
    _block_telegram(res)
    return False, None, False


def _render_user(uid):
    now = time.time()
    with _lock:
        rec = _users.get(uid)
        _dirty_render.pop(uid, None)
        if rec is None:
            return
        snapshot = copy.deepcopy(rec)
    ok, message_id, created = _upsert_message(snapshot.get("message_id"), build_user_text(snapshot, now))
    with _lock:
        rec = _users.get(uid)
        if rec is None:
            return
        if message_id != rec.get("message_id"):
            rec["message_id"] = message_id
            _dirty_persist.add(uid)
        if ok:
            rec["msg_online"] = _is_online(snapshot, now)
            _last_render[uid] = now
            _dirty_persist.add(uid)
        else:
            _dirty_render.setdefault(uid, (now, False))   # إعادة المحاولة لاحقًا
        if created:
            global _urgent_persist
            _urgent_persist = True   # رقم الرسالة الجديدة يُحفَظ فورًا كي لا تتكرر بعد إعادة التشغيل


def _render_dashboard():
    """يُعدّل رسالة لوحة الإحصائيات العامة (أو يُنشئها إن لم تكن موجودة).

    عند الفشل، يزيد _dashboard_fail_streak بدل الاكتفاء بوضع علامة
    "قذرة" فقط؛ هذا العدّاد يُستخدَم في _telegram_step لتباعد إعادة
    المحاولة تصاعديًا، بدل إغراق تيليغرام بمحاولة كل عشرين ثانية للأبد
    عندما يكون الفشل دائمًا لا عابرًا (انظر الشرح في أعلى الملف)."""
    global _dashboard_dirty, _dashboard_last, _global_dirty, _urgent_persist, _dashboard_fail_streak
    now = time.time()
    with _lock:
        _dashboard_dirty = False
        message_id = _global.get("dashboard_message_id")
    ok, new_id, created = _upsert_message(message_id, build_dashboard_text(now))
    with _lock:
        if new_id != _global.get("dashboard_message_id"):
            _global["dashboard_message_id"] = new_id
            _global_dirty = True
        if ok:
            _dashboard_last = now
            _dashboard_fail_streak = 0
        else:
            _dashboard_dirty = True
            _dashboard_fail_streak += 1
        if created:
            _urgent_persist = True
    if created and new_id:  # تثبيت لوحة الإحصائيات أعلى الشات (اختياري، الفشل غير مهم)
        _tg("pinChatMessage", {"chat_id": NOTIFIER_CHAT_ID, "message_id": new_id,
                                "disable_notification": True})


def _sweep_online(now):
    """من تغيّرت حالته (متصل <-> غير متصل) عن المعروض في رسالته يُجدَّد."""
    global _dashboard_dirty, _last_sweep
    _last_sweep = now
    with _lock:
        for uid, rec in _users.items():
            if _is_online(rec, now) != bool(rec.get("msg_online")):
                first_ts, _imp = _dirty_render.get(uid, (now, False))
                _dirty_render[uid] = (first_ts, True)
                _dashboard_dirty = True


def _telegram_step(now):
    """خطوة واحدة من الخيط الخلفي: إما تنبيه فوري، أو تعديل رسالة مستخدم،
    أو تعديل لوحة الإحصائيات -- بحد أقصى تعديل واحد لكل استدعاء (حد
    تيليغرام تقريبًا رسالة واحدة/ثانية).

    الأولوية دائمًا لتحديث رسائل المستخدمين الفردية (candidates) على
    لوحة الإحصائيات العامة (dashboard_due). هذا مقصود: لوحة الإحصائيات
    تُعتبر "مستحقة" (dirty) عمليًا مع كل ضغطة زر تقريبًا، فلو أُعطيت
    الأولوية (كما كان الحال سابقًا) وكان تعديلها يفشل بشكل دائم لأي سبب،
    لَدخل النظام في حلقة تحاول اللوحة أولًا للأبد ولا تصل أبدًا لتحديث
    أي رسالة مستخدم -- وهو بالضبط ما كان يُسبّب بقاء عدّادات الجداول
    وآخر نشاط ثابتة عند الصفر رغم تحدّثها الصحيح في الذاكرة."""
    global _dashboard_dirty
    if not _notify_enabled:
        with _lock:
            _dirty_render.clear()
            _alerts.clear()
            _dashboard_dirty = False
        return
    if now < _tg_blocked_until:
        return

    with _lock:
        alert = _alerts.pop(0) if _alerts else None
    if alert:
        res = _tg("sendMessage", {"chat_id": NOTIFIER_CHAT_ID, "text": alert})
        if not res["ok"]:
            with _lock:
                _alerts.insert(0, alert)
            _block_telegram(res)
        return

    with _lock:
        # تباعد تصاعدي لإعادة محاولة اللوحة عند الفشل المتكرر: 20 ثانية،
        # 40، 80، ... حتى سقف DASHBOARD_MAX_BACKOFF_SECONDS.
        backoff = min(
            DASHBOARD_MAX_BACKOFF_SECONDS,
            MIN_DASHBOARD_INTERVAL * (2 ** _dashboard_fail_streak),
        )
        dashboard_due = _dashboard_dirty and now - _dashboard_last >= backoff
        candidates = [(not important, first_ts, uid)
                      for uid, (first_ts, important) in _dirty_render.items()
                      if now - _last_render.get(uid, 0.0) >= MIN_USER_EDIT_INTERVAL]

    if candidates:
        _render_user(min(candidates)[2])
    elif dashboard_due:
        _render_dashboard()


def _tick():
    now = time.time()
    if not _ready:
        if now - _last_load_try >= LOAD_RETRY_INTERVAL:
            _try_load()
        return
    if now - _last_sweep >= SWEEP_INTERVAL_SECONDS:
        _sweep_online(now)
    if _urgent_persist or now - _last_persist >= PERSIST_INTERVAL_SECONDS:
        _persist_pending()
    _telegram_step(now)


def _worker_loop():
    while not _stop_event.is_set():
        try:
            _tick()
        except Exception:  # noqa: BLE001
            logger.exception("خطأ غير متوقع في الخيط الخلفي للإشعارات")
        _stop_event.wait(TICK_SECONDS)


# ---------------------------------------------------------------------------
# الواجهة العامة (كلها سريعة وبلا أي طلب شبكي، آمنة للاستدعاء من حلقة البوت)
# ---------------------------------------------------------------------------

def start():
    """يُستدعى مرة واحدة عند تشغيل البوت: يحمّل البيانات ويبدأ الخيط الخلفي."""
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    if _storage_enabled:
        for attempt in range(3):
            if _try_load():
                break
            time.sleep(2 * (attempt + 1))
        else:
            logger.error("سيُعاد تحميل البيانات تلقائيًا كل %s ثانية؛ التتبّع متوقف مؤقتًا "
                         "(لحماية بياناتك من الكتابة فوقها بسجلات فارغة).", LOAD_RETRY_INTERVAL)
    _stop_event.clear()
    _worker = threading.Thread(target=_worker_loop, name="notifier-worker", daemon=True)
    _worker.start()


def shutdown():
    """حفظ نهائي قبل الإغلاق (يُستدعى عند إيقاف الخدمة)."""
    _stop_event.set()
    if _ready:
        _persist_pending()


def track_activity(user, action=None):
    """يُسجَّل عند كل تفاعل (أمر أو ضغطة زر): مستخدم جديد، نشاط، جلسة جديدة."""
    if not _ready or user is None:
        return
    now = time.time()
    uid = int(user.id)
    username = getattr(user, "username", None)
    full_name = getattr(user, "full_name", None)
    with _lock:
        rec = _users.get(uid)
        is_new = rec is None
        if is_new:
            rec = _normalize_record({
                "telegram_id": uid, "user_no": _allocate_user_no(),
                "username": username, "full_name": full_name, "join_ts": now,
            })
            _users[uid] = rec
        starts_session = not _is_online(rec, now)
        if starts_session:
            rec["sessions"] += 1
            _global["hour_sessions"][datetime.fromtimestamp(now, DAMASCUS_TZ).hour] += 1
            global _global_dirty
            _global_dirty = True
        rec["last_active_ts"] = now
        rec["clicks"] += 1
        rec["username"] = username or rec.get("username")
        rec["full_name"] = full_name or rec.get("full_name")
        if action:
            rec["last_action"] = action
            rec["last_action_ts"] = now
        _mark_dirty(uid, important=is_new or starts_session, urgent=is_new)


def record_feature(user_id, feature):
    """feature: selected_schedule | all_times | optimal  (تُسجَّل بعد نجاح التنفيذ)."""
    if not _ready or feature not in FEATURES:
        return
    with _lock:
        rec = _users.get(int(user_id))
        if rec is None:
            return
        rec["counts"][feature] += 1
        _mark_dirty(int(user_id), important=True)


def record_course(course_name):
    """إحصاء أكثر المواد اختيارًا (للوحة الإحصائيات)."""
    if not _ready or not course_name:
        return
    global _global_dirty, _dashboard_dirty
    with _lock:
        counts = _global["course_counts"]
        counts[course_name] = counts.get(course_name, 0) + 1
        _global_dirty = True
        _dashboard_dirty = True


def notify_update_result(success: bool, detail: str = "") -> None:
    """نتيجة تحديث أوقات الجدول: تظهر في اللوحة، والفشل يُرسَل كتنبيه مستقل."""
    global _global_dirty, _dashboard_dirty
    with _lock:
        upd = _global["update"]
        upd["ok" if success else "fail"] += 1
        upd.update({"last_ts": time.time(), "last_ok": bool(success),
                    "last_detail": (detail or "")[:200]})
        _global_dirty = True
        _dashboard_dirty = True
        if not success:
            _alerts.append("⚠️ تحديث أوقات الجدول فشل." + (f"\nالتفاصيل: {detail}" if detail else ""))


def is_owner(user_id):
    return bool(NOTIFIER_CHAT_ID) and str(user_id) == str(NOTIFIER_CHAT_ID)


def request_resync():
    """يعيد بناء كل رسائل المستخدمين واللوحة تدريجيًا (بمعدل ≈ رسالة/ثانية)."""
    global _dashboard_dirty
    with _lock:
        for uid in _users:
            _dirty_render.setdefault(uid, (time.time(), False))
        _dashboard_dirty = True


# ---------------------------------------------------------------------------
# التصدير والاستيراد (نسخة احتياطية قابلة للمراجعة والإكمال)
# ---------------------------------------------------------------------------

CSV_COLUMNS = ["user_no", "telegram_id", "username", "full_name", "join_date", "last_active",
               "sessions", "clicks", "selected_schedule", "all_times", "optimal", "last_action"]


def build_export_files():
    """يعيد (JSON كامل للنسخ الاحتياطي، CSV يفتح في Excel) كبايتات."""
    with _lock:
        users = sorted(copy.deepcopy(list(_users.values())), key=lambda r: r["user_no"] or 0)
        glob = copy.deepcopy(_global)
    payload = {"version": 2, "exported_at": datetime.now(DAMASCUS_TZ).isoformat(),
               "global": glob, "users": users}
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in users:
        writer.writerow([r["user_no"], r["telegram_id"], r.get("username") or "", r.get("full_name") or "",
                         _fmt_date(r["join_ts"]), _fmt_dt(r["last_active_ts"]), r["sessions"], r["clicks"],
                         r["counts"]["selected_schedule"], r["counts"]["all_times"], r["counts"]["optimal"],
                         r.get("last_action") or ""])
    return json_bytes, buf.getvalue().encode("utf-8-sig")


def import_backup(raw_bytes):
    """يدمج نسخة JSON سبق تصديرها: يضيف الناقص ويُكمل الحقول الفارغة، ولا يمسح
    أي شيء موجود ولا ينقص أي عدّاد. يعيد (عدد المضاف، عدد المدموج)."""
    global _global_dirty, _dashboard_dirty, _urgent_persist
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
        items = payload["users"]
        if not isinstance(items, list):
            raise TypeError
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise ValueError("الملف ليس نسخة احتياطية صالحة من هذا البوت.")

    added = merged = 0
    now = time.time()
    with _lock:
        taken = {r["user_no"] for r in _users.values() if r["user_no"] is not None}
        for item in items:
            try:
                uid = int(item["telegram_id"])
                incoming = _normalize_record(dict(item))
            except (KeyError, TypeError, ValueError):
                continue
            current = _users.get(uid)
            if current is None:
                if incoming["user_no"] is None or incoming["user_no"] in taken:
                    incoming["user_no"] = _allocate_user_no()
                taken.add(incoming["user_no"])
                _users[uid] = incoming
                added += 1
            else:
                for key in ("username", "full_name", "message_id", "user_no"):
                    current[key] = current.get(key) or incoming.get(key)
                for f in FEATURES:
                    current["counts"][f] = max(current["counts"][f], incoming["counts"][f])
                current["clicks"] = max(current["clicks"], incoming["clicks"])
                current["sessions"] = max(current["sessions"], incoming["sessions"])
                current["last_active_ts"] = max(current["last_active_ts"], incoming["last_active_ts"])
                joins = [t for t in (current["join_ts"], incoming["join_ts"]) if t]
                current["join_ts"] = min(joins) if joins else 0.0
                merged += 1
            _dirty_persist.add(uid)
            _dirty_render.setdefault(uid, (now, False))

        other = _normalize_global(payload.get("global"))
        for name, n in other["course_counts"].items():
            _global["course_counts"][name] = max(_global["course_counts"].get(name, 0), n)
        _global["hour_sessions"] = [max(a, b) for a, b in zip(_global["hour_sessions"], other["hour_sessions"])]
        top = max([r["user_no"] or 0 for r in _users.values()] or [0])
        _global["next_user_no"] = max(_global["next_user_no"], other["next_user_no"], top + 1)
        _global_dirty = True
        _dashboard_dirty = True
        _urgent_persist = True
    return added, merged
