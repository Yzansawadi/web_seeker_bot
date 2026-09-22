#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_notifier_fake_redis.py
----------------------------
تنفيذ مصغّر في الذاكرة لأوامر Redis التي يستخدمها notifier.py، حتى تُختبر
منطق المتابعة (الأرقام الدائمة، العدادات، الفهارس، الترحيل) دون أي اتصال
شبكي فعلي بـ Upstash.

ليست محاكاة كاملة لـ Redis: تكفي الأوامر المستخدمة فعليًا في المشروع، وبسلوك
كافٍ للتحقق من صحة المنطق (ذرّية الأوامر، ترتيب ZSET، تنسيق القيم كنصوص كما
يعيدها Redis الحقيقي).
"""

import fnmatch


def _format_number(value):
    """Redis يعيد الأعداد كنصوص، وللأعداد الصحيحة بلا كسور عشرية."""
    number = float(value)
    if number == int(number):
        return str(int(number))
    return str(number)


def _parse_bound(value, default):
    if isinstance(value, str):
        if value in ("-inf", "-Infinity"):
            return float("-inf")
        if value in ("+inf", "Infinity"):
            return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class FakeRedis:
    def __init__(self):
        self.strings = {}
        self.zsets = {}
        self.hashes = {}
        self.command_log = []

    # -- أدوات مساعدة -------------------------------------------------------

    def get_int(self, key, default=0):
        raw = self.strings.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def _zset(self, key):
        return self.zsets.setdefault(key, {})

    def _sorted_members(self, key, reverse=False):
        members = self._zset(key)
        return sorted(members.items(), key=lambda item: (item[1], item[0]), reverse=reverse)

    def _slice(self, items, start, stop):
        if stop == -1:
            stop = len(items)
        else:
            stop = int(stop) + 1
        return items[int(start):stop]

    # -- التنفيذ ------------------------------------------------------------

    def execute(self, command):
        self.command_log.append(list(command))
        op = str(command[0]).upper()
        args = command[1:]
        handler = getattr(self, f"_{op.lower()}", None)
        if handler is None:
            raise NotImplementedError(f"الأمر غير مدعوم في FakeRedis: {op}")
        return handler(args)

    def _get(self, args):
        return self.strings.get(args[0])

    def _set(self, args):
        self.strings[args[0]] = args[1] if isinstance(args[1], str) else str(args[1])
        return "OK"

    def _incr(self, args):
        value = self.get_int(args[0]) + 1
        self.strings[args[0]] = str(value)
        return value

    def _del(self, args):
        removed = 0
        for key in args:
            for store in (self.strings, self.zsets, self.hashes):
                if store.pop(key, None) is not None:
                    removed += 1
        return removed

    def _zadd(self, args):
        key, score, member = args[0], float(args[1]), str(args[2])
        members = self._zset(key)
        is_new = member not in members
        members[member] = score
        return 1 if is_new else 0

    def _zcard(self, args):
        return len(self._zset(args[0]))

    def _zcount(self, args):
        low = _parse_bound(args[1], float("-inf"))
        high = _parse_bound(args[2], float("inf"))
        return sum(1 for score in self._zset(args[0]).values() if low <= score <= high)

    def _zincrby(self, args):
        key, incr, member = args[0], float(args[1]), str(args[2])
        members = self._zset(key)
        members[member] = members.get(member, 0.0) + incr
        return _format_number(members[member])

    def _zrange(self, args):
        return self._range(args, reverse=False)

    def _zrevrange(self, args):
        return self._range(args, reverse=True)

    def _range(self, args, reverse):
        key, start, stop = args[0], args[1], args[2]
        with_scores = any(str(a).upper() == "WITHSCORES" for a in args[3:])
        items = self._slice(self._sorted_members(key, reverse), start, stop)
        if not with_scores:
            return [member for member, _ in items]
        result = []
        for member, score in items:
            result.extend([member, _format_number(score)])
        return result

    def _zremrangebyscore(self, args):
        key = args[0]
        low = _parse_bound(args[1], float("-inf"))
        high = _parse_bound(args[2], float("inf"))
        members = self._zset(key)
        doomed = [m for m, score in members.items() if low <= score <= high]
        for member in doomed:
            del members[member]
        return len(doomed)

    def _hincrby(self, args):
        key, field, incr = args[0], str(args[1]), int(args[2])
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = bucket.get(field, 0) + incr
        return bucket[field]

    def _hset(self, args):
        key, field, value = args[0], str(args[1]), args[2]
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = int(value)
        return 1

    def _hgetall(self, args):
        result = []
        for field, value in self.hashes.get(args[0], {}).items():
            result.extend([field, str(value)])
        return result

    def _scan(self, args):
        cursor = str(args[0])
        pattern = None
        if len(args) >= 3 and str(args[1]).upper() == "MATCH":
            pattern = str(args[2])
        keys = [k for k in self.strings if not pattern or fnmatch.fnmatch(k, pattern)]
        # دفعة واحدة تكفي: عدد المستخدمين في الاختبارات صغير، وإرجاع المؤشر "0"
        # يعني انتهاء المسح — وهو ما تتوقعه حلقة _scan_user_keys.
        return ["0", sorted(keys)]
