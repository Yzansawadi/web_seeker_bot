#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schedule_data.py
-----------------
Loads subjects.xlsx (course -> year mapping) and IUST_schedule_full.xlsx
(scraped session data) and merges them into a clean in-memory structure
that the Telegram bot can use.

This module is re-imported / re-read fresh every time the bot needs data,
so that after extract_schedule.py regenerates IUST_schedule_full.xlsx the
bot automatically picks up the new data with no restart required.
"""

import os
import re
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBJECTS_PATH = os.path.join(BASE_DIR, "subjects.xlsx")
SCHEDULE_PATH = os.path.join(BASE_DIR, "IUST_schedule_full.xlsx")

# Day ordering used everywhere we need to sort/display days (Sat -> Thu)
DAY_ORDER = [
    "السبت",      # Saturday
    "الأحد",      # Sunday
    "الاثنين",    # Monday
    "الثلاثاء",   # Tuesday
    "الأربعاء",   # Wednesday
    "الخميس",     # Thursday
    "الجمعة",     # Friday
]

DAY_EN = {
    "السبت": "Saturday",
    "الأحد": "Sunday",
    "الاثنين": "Monday",
    "الثلاثاء": "Tuesday",
    "الأربعاء": "Wednesday",
    "الخميس": "Thursday",
    "الجمعة": "Friday",
}

ACTIVITY_EN = {
    "نظري": "Theoretical",
    "عملي": "Practical",
}


def _time_to_minutes(t):
    """Convert '8:00 AM' / '12:00 PM' style string to minutes-since-midnight for sorting."""
    if not t:
        return 24 * 60  # unknown times sort last
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", t.strip(), re.I)
    if not m:
        return 24 * 60
    h, mins, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if ampm == "AM":
        h = 0 if h == 12 else h
    else:
        h = 12 if h == 12 else h + 12
    return h * 60 + mins


def _split_days(days_field):
    """
    The scraper sometimes stores more than one day in a single cell,
    e.g. 'الثلاثاء / السبت'. Split that into a clean list of individual days.
    """
    if not days_field:
        return []
    parts = [d.strip() for d in str(days_field).split("/")]
    return [p for p in parts if p]


def _load_courses_uncached():
    """
    Returns:
        years: dict {year_int: {code_str: course_dict}}
        where course_dict = {
            "code": str,
            "name": str,                # canonical name from subjects.xlsx
            "year": int,
            "sessions": [               # one entry per (day, activity, time slot)
                {
                    "day": "السبت",
                    "activity": "نظري" | "عملي",
                    "start": "8:00 AM",
                    "end": "12:00 PM",
                    "start_min": int,
                    "room": str,
                    "section": str,
                    "teacher": str,
                },
                ...
            ]
        }

    Courses that exist in the schedule file but are not found in
    subjects.xlsx are placed under a synthetic year key 0 ("Unclassified")
    so no scraped data is silently dropped.
    """
    subj_df = pd.read_excel(SUBJECTS_PATH)
    subj_df.columns = ["year", "code", "name"]

    # Build code -> (year, name) map. Skip rows with no course code -- those
    # courses have nothing to schedule (e.g. language / computer skills).
    code_to_info = {}
    for _, row in subj_df.iterrows():
        if pd.isna(row["code"]):
            continue
        code_str = str(int(row["code"]))
        code_to_info[code_str] = {
            "year": int(row["year"]),
            "name": str(row["name"]).strip(),
        }

    years = {}

    # Make sure every course from subjects.xlsx exists in `years`, even before
    # any schedule data has ever been fetched. This way the bot can still show
    # year/course lists and a clear "no schedule yet" message instead of
    # crashing when IUST_schedule_full.xlsx doesn't exist yet.
    for code_str, info in code_to_info.items():
        year = info["year"]
        years.setdefault(year, {})
        years[year].setdefault(
            code_str, {"code": code_str, "name": info["name"], "year": year, "sessions": []}
        )

    if not os.path.exists(SCHEDULE_PATH):
        # No scraped data yet -- return the subjects-only skeleton so the bot
        # can still walk users through years/courses and tell them to press
        # "Update Schedule Times" first.
        return years

    sched_df = pd.read_excel(SCHEDULE_PATH)
    sched_df.columns = [
        "code", "name", "activity", "days", "start_time",
        "end_time", "room", "section", "teacher",
    ]

    for _, row in sched_df.iterrows():
        raw_code = row["code"]
        if pd.isna(raw_code):
            continue
        code_str = str(raw_code).strip()
        # normalise things like "101201.0" -> "101201"
        try:
            code_str = str(int(float(code_str)))
        except ValueError:
            pass

        info = code_to_info.get(code_str)
        if info:
            year = info["year"]
            name = info["name"]
        else:
            # Found in schedule but not in subjects.xlsx -> bucket it so
            # nothing is lost, instead of dropping the row.
            year = 0
            name = str(row["name"]).strip()

        years.setdefault(year, {})
        course = years[year].setdefault(
            code_str, {"code": code_str, "name": name, "year": year, "sessions": []}
        )

        activity = str(row["activity"]).strip()
        start = str(row["start_time"]).strip() if pd.notna(row["start_time"]) else ""
        end = str(row["end_time"]).strip() if pd.notna(row["end_time"]) else ""
        room = str(row["room"]).strip() if pd.notna(row["room"]) else ""
        section = str(row["section"]).strip() if pd.notna(row["section"]) else ""
        teacher = str(row["teacher"]).strip() if pd.notna(row["teacher"]) else ""

        for day in _split_days(row["days"]):
            course["sessions"].append({
                "day": day,
                "activity": activity,
                "start": start,
                "end": end,
                "start_min": _time_to_minutes(start),
                "room": room,
                "section": section,
                "teacher": teacher,
            })

    # Sort each course's sessions by day order then start time, for stable display
    def day_index(d):
        return DAY_ORDER.index(d) if d in DAY_ORDER else len(DAY_ORDER)

    for year_courses in years.values():
        for course in year_courses.values():
            course["sessions"].sort(key=lambda s: (day_index(s["day"]), s["start_min"]))

    return years


_cache = {"years": None, "mtimes": None}


def _current_mtimes():
    """يأخذ بصمة بسيطة (وقت آخر تعديل) لكل ملف بيانات لمعرفة إن وجب إعادة التحميل."""
    def mtime(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return None
    return (mtime(SUBJECTS_PATH), mtime(SCHEDULE_PATH))


def load_courses(force_reload=False):
    """
    واجهة محمَّلة بذاكرة تخزين مؤقت (cache) حول _load_courses_uncached().

    بدون هذا التخزين المؤقت، كانت كل ضغطة زر في البوت تعيد قراءة وتحليل
    ملفي Excel بالكامل من القرص، وهذا أبطأ استجابة البوت بشكل ملحوظ.
    الآن تُحمَّل البيانات مرة واحدة فقط وتبقى في الذاكرة، ولا تُعاد قراءتها
    من القرص إلا إذا:
      - تغيّر وقت تعديل subjects.xlsx أو IUST_schedule_full.xlsx (مثلًا بعد
        تشغيل extract_schedule.py بنجاح)، أو
      - تم تمرير force_reload=True صريحًا.
    """
    current_mtimes = _current_mtimes()
    if (
        not force_reload
        and _cache["years"] is not None
        and _cache["mtimes"] == current_mtimes
    ):
        return _cache["years"]

    years = _load_courses_uncached()
    _cache["years"] = years
    _cache["mtimes"] = current_mtimes
    return years


def get_years(years_data):
    """Returns sorted list of real academic years (1-5) present in the data."""
    return sorted(y for y in years_data.keys() if y != 0)


def get_courses_for_year(years_data, year):
    """Returns list of course dicts for a given year, sorted by name."""
    courses = years_data.get(year, {})
    return sorted(courses.values(), key=lambda c: c["name"])


def get_course(years_data, year, code):
    return years_data.get(year, {}).get(code)
