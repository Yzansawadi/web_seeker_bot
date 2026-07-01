#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schedule_optimizer.py
-----------------------
محرك توليد "الجدول المثالي": يأخذ قائمة المواد التي اختارها الطالب، ولكل
مادة قد توجد شُعبة (أو أكثر) لكل نوع نشاط (نظري/عملي)، ويبحث عن أفضل
تركيبة من الشُعب (شُعبة واحدة لكل نوع نشاط موجود لكل مادة) بحيث:

    قيود صارمة (يجب تحقّقها دائمًا):
        1. عدم وجود أي تعارض زمني بين أي جلستين في الجدول النهائي.
        2. النظري والعملي لنفس المادة مستقلان تمامًا عن بعضهما (لكل واحد
           شُعبته الخاصة، لا ربط بينهما إلا أنهما لنفس المادة).
        3. إذا كانت شُعبة موزّعة على يومين، يُؤخذ اليومان معًا بنفس الوقت
           (هذا مضمون أصلاً من شكل البيانات في schedule_data.py، حيث كل
           شُعبة تُمثَّل كمجموعة جلسات بنفس الوقت على يوم أو يومين).

    أهداف يُحسَّن الجدول بحسبها (بالأولوية، من الأهم للأقل أهمية):
        1. أقل عدد أيام حضور إجمالي (الهدف الأهم).
        2. أقل مجموع فراغات بين الحصص ضمن اليوم الواحد.
        3. أبكر وقت بدء ممكن لكل يوم (تفضيل الأوقات الباكرة).

الخوارزمية: بحث رجوعي (Backtracking) مع تقليم بطريقة Branch and Bound،
يُرتَّب فيه المتغيّرات (مادة × نوع نشاط) من الأكثر تقييدًا للأقل (أقل عدد
شُعب أولاً) لتسريع التقليم، ويُحتفَظ بأفضل N حل (k-best) للسماح للطالب
باختيار بديل إن رغب. يحمل البحث حدًا زمنيًا (افتراضيًا بضع ثوانٍ) فيعيد
أفضل ما توصّل إليه حتى تلك اللحظة إن لم يكتمل البحث الشامل (خوارزمية
"anytime" تضمن استجابة البوت دائمًا في وقت معقول).

إن لم يوجد أي تركيبة خالية من التعارض لكل المواد المختارة معًا، يتدرّج
المحرك تلقائيًا: يستثني أقل عدد ممكن من المواد (تلك الأكثر تسببًا
بالتعارض) ليجد أفضل جدول ممكن للباقي، ويُرجع تقريرًا بالمواد المستثناة
وسبب ذلك.
"""

import time as _time
from dataclasses import dataclass, field
from itertools import combinations


# ---------------------------------------------------------------------------
# تمثيل الشُعب والاختيارات
# ---------------------------------------------------------------------------

@dataclass
class SectionOption:
    """شُعبة واحدة قابلة للاختيار (نظري أو عملي) لمادة معيّنة. قد تحتوي جلسة
    واحدة (يوم واحد) أو جلستين (موزّعة على يومين بنفس الوقت)."""
    course_code: str
    course_name: str
    activity: str
    section_id: str
    sessions: list = field(default_factory=list)  # كل عنصر = dict من schedule_data


@dataclass
class Variable:
    """متغيّر بحث واحد: مادة + نوع نشاط معيّن، مع كل الشُعب الممكنة له."""
    course_code: str
    course_name: str
    activity: str
    options: list  # قائمة SectionOption


def _time_overlap(s1, e1, s2, e2):
    return s1 < e2 and s2 < e1


def build_variables(years_data, selected_list, get_course_fn):
    """
    يبني قائمة المتغيّرات (مادة × نوع نشاط) من قائمة المواد المختارة.
    المواد التي لا تملك أي معلومات وقت (sessions فارغة) تُستبعَد من
    التحسين تمامًا (لا تتعارض مع شيء، ولا يمكن جدولتها أساسًا)، ويُعاد
    اسمها ضمن "ملاحظات" لإخبار الطالب.

    get_course_fn: دالة (year, code) -> course dict، لتفادي ربط هذه
    الوحدة مباشرة بواجهة schedule_data (سهولة اختبار واستبدال).
    """
    variables = []
    no_data_courses = []

    for year, code in selected_list:
        course = get_course_fn(years_data, year, code)
        if course is None:
            continue
        if not course["sessions"]:
            no_data_courses.append(course["name"])
            continue

        by_activity = {}
        for s in course["sessions"]:
            by_activity.setdefault(s["activity"], {}).setdefault(s["section"], []).append(s)

        for activity, sections in by_activity.items():
            options = [
                SectionOption(
                    course_code=code,
                    course_name=course["name"],
                    activity=activity,
                    section_id=section_id,
                    sessions=sessions_list,
                )
                for section_id, sessions_list in sections.items()
            ]
            variables.append(Variable(
                course_code=code, course_name=course["name"], activity=activity, options=options,
            ))

    return variables, no_data_courses


# ---------------------------------------------------------------------------
# دوال التقييم (الأهداف الثلاثة)
# ---------------------------------------------------------------------------

def _compute_score(chosen_options):
    """
    يحسب (days_count, total_gap_minutes, earliness_score) لتركيبة شُعب
    مكتملة. يُستخدم tuple مباشرةً للمقارنة لأن مقارنة المجموعات في
    بايثون تتم بترتيب معجمي (lexicographic) تلقائيًا، فهذا يضمن أولوية
    "أقل أيام" أولاً، ثم "أقل فراغات"، ثم "أبكر بدء"، دون الحاجة لأوزان
    عددية قد تتداخل.
    """
    by_day = {}
    for opt in chosen_options:
        for s in opt.sessions:
            by_day.setdefault(s["day"], []).append(s)

    days_count = len(by_day)
    total_gap = 0
    earliness = 0

    for day, sessions in by_day.items():
        ordered = sorted(sessions, key=lambda s: s["start_min"])
        earliness += ordered[0]["start_min"]
        for i in range(1, len(ordered)):
            prev_end = ordered[i - 1]["start_min"] + _duration(ordered[i - 1])
            gap = ordered[i]["start_min"] - prev_end
            if gap > 0:
                total_gap += gap

    return (days_count, total_gap, earliness)


def _duration(session):
    """مدة الجلسة بالدقائق (تُحسَب وتُخزَّن مرة واحدة في duration_min عبر
    _annotate_durations قبل بدء البحث، لتفادي إعادة الحساب آلاف المرات)."""
    return session.get("duration_min", 60)


def _annotate_durations(variables, time_to_minutes_fn):
    """يضيف duration_min (بالدقائق) لكل جلسة مرة واحدة قبل البحث. يُخزَّن
    هذا الحقل على نفس قاموس الجلسة المُعاد من schedule_data (الذي قد يكون
    مُخزَّنًا مؤقتًا/مشتركًا بين استدعاءات متعددة)، فتُحسَب القيمة فعليًا
    مرة واحدة فقط طوال عمر الكاش، لا مع كل استدعاء لهذه الوحدة."""
    for var in variables:
        for opt in var.options:
            for s in opt.sessions:
                if "duration_min" not in s:
                    end_abs = time_to_minutes_fn(s["end"])
                    s["duration_min"] = max(0, end_abs - s["start_min"])


# ---------------------------------------------------------------------------
# فحص التعارض
# ---------------------------------------------------------------------------

def _conflicts_with_busy(option, busy_by_day):
    """يتحقق إن كانت شُعبة معيّنة تتعارض مع أي جلسة موضوعة مسبقًا."""
    for s in option.sessions:
        day = s["day"]
        start = s["start_min"]
        end = start + _duration(s)
        for (bstart, bend) in busy_by_day.get(day, []):
            if _time_overlap(start, end, bstart, bend):
                return True
    return False


def _add_to_busy(option, busy_by_day):
    added = []
    for s in option.sessions:
        day = s["day"]
        start = s["start_min"]
        end = start + _duration(s)
        busy_by_day.setdefault(day, []).append((start, end))
        added.append((day, (start, end)))
    return added


def _remove_from_busy(added, busy_by_day):
    for day, interval in added:
        busy_by_day[day].remove(interval)


# ---------------------------------------------------------------------------
# البحث الرجوعي مع k-best وحد زمني
# ---------------------------------------------------------------------------

class _SearchState:
    def __init__(self, top_n, time_budget_seconds):
        self.top_n = top_n
        self.deadline = _time.time() + time_budget_seconds
        self.best = []  # قائمة (score_tuple, assignment) مرتبة تصاعديًا، بحد top_n
        self.nodes_explored = 0
        self.timed_out = False

    def time_up(self):
        if _time.time() > self.deadline:
            self.timed_out = True
        return self.timed_out

    def worst_best_score(self):
        if len(self.best) < self.top_n:
            return None  # لا حد علوي بعد، القائمة لم تمتلئ
        return self.best[-1][0]

    def consider(self, score, assignment):
        if len(self.best) < self.top_n:
            self.best.append((score, list(assignment)))
            self.best.sort(key=lambda x: x[0])
        elif score < self.best[-1][0]:
            self.best[-1] = (score, list(assignment))
            self.best.sort(key=lambda x: x[0])


def _partial_lower_bound(busy_by_day):
    """أقل قيمة ممكنة لعدد الأيام في أي حل كامل بناءً على الأيام المستخدَمة
    حتى الآن جزئيًا (لا يمكن أن يقل عدد الأيام النهائي عن هذا أبدًا)."""
    return len({day for day, intervals in busy_by_day.items() if intervals})


def _search(variables, state, index, assignment, busy_by_day):
    if state.time_up():
        return

    state.nodes_explored += 1

    if index == len(variables):
        score = _compute_score(assignment)
        state.consider(score, assignment)
        return

    worst = state.worst_best_score()
    if worst is not None:
        partial_days = _partial_lower_bound(busy_by_day)
        if (partial_days,) > worst[:1]:
            return  # تقليم: لا يمكن لهذا الفرع أن يتفوّق على أسوأ ما في أفضل N حتى الآن

    var = variables[index]
    # ترتيب الخيارات: نفضّل أولاً ما لا يضيف يومًا جديدًا، ثم الأبكر بدءًا،
    # لزيادة فرصة الوصول لحل جيد مبكرًا (يُحسّن فعالية التقليم اللاحق).
    existing_days = {d for d, ivs in busy_by_day.items() if ivs}

    def option_heuristic(opt):
        opt_days = {s["day"] for s in opt.sessions}
        adds_new_day = len(opt_days - existing_days) > 0
        earliest = min(s["start_min"] for s in opt.sessions)
        return (adds_new_day, earliest)

    ordered_options = sorted(var.options, key=option_heuristic)

    for opt in ordered_options:
        if state.time_up():
            return
        if _conflicts_with_busy(opt, busy_by_day):
            continue
        added = _add_to_busy(opt, busy_by_day)
        assignment.append(opt)
        _search(variables, state, index + 1, assignment, busy_by_day)
        assignment.pop()
        _remove_from_busy(added, busy_by_day)


def _run_search(variables, top_n, time_budget_seconds):
    # المتغيّرات الأكثر تقييدًا أولًا (أقل عدد شُعب) يحسّن التقليم كثيرًا.
    ordered_vars = sorted(variables, key=lambda v: len(v.options))
    state = _SearchState(top_n=top_n, time_budget_seconds=time_budget_seconds)
    _search(ordered_vars, state, 0, [], {})
    return state


# ---------------------------------------------------------------------------
# تشخيص حالة "لا يوجد حل كامل" وإيجاد أكبر مجموعة فرعية قابلة للجدولة
# ---------------------------------------------------------------------------

def _course_keys(variables):
    seen = []
    for v in variables:
        key = v.course_code
        if key not in seen:
            seen.append(key)
    return seen


def _variables_for_courses(variables, course_codes):
    keep = set(course_codes)
    return [v for v in variables if v.course_code in keep]


def _has_any_full_solution(variables, time_budget_seconds):
    state = _run_search(variables, top_n=1, time_budget_seconds=time_budget_seconds)
    return len(state.best) > 0, state


def _find_max_feasible_subset(variables, course_codes, deadline):
    """
    يبحث عن أكبر مجموعة فرعية من course_codes يمكن إيجاد جدول كامل لها
    بدون أي تعارض، بالتدرّج الصحيح رياضيًا: يجرّب أولاً استبعاد مادة واحدة
    (كل التركيبات الممكنة لاستبعاد مادة واحدة، لا مادة واحدة محدَّدة فقط)،
    فإن فشلت كلها يجرّب استبعاد مادتين معًا، وهكذا.

    هذا يصحّح قِصَر نظر النهج الجشع السابق (الذي كان يستثني مادة "تبدو"
    الأكثر تعارضًا بناءً على إشارة تقريبية، فقد يستثني أكثر من اللازم في
    حالات لا تكون فيها المادة "الأكثر تعارضًا ظاهريًا" هي فعلاً المادة
    الصحيحة لاستبعادها للوصول لأكبر مجموعة ممكنة). البحث هنا يضمن إيجاد
    العدد الأقصى الحقيقي من المواد القابلة للجدولة معًا، ضمن الوقت المتاح.

    عند تعادل عدة تركيبات بنفس عدد المواد المستبعدة، تُفضَّل التركيبة التي
    تستبعد المواد الأحدث في ترتيب الاختيار الأصلي (تُبقي المواد الأولى
    التي اختارها الطالب قدر الإمكان)، لأن هذا أكثر توقعًا وعدلًا للمستخدم
    من اختيار عشوائي بين تركيبات متعادلة.

    يعيد (set(الرموز المُبقاة), state) أو (None, None) إن لم يُوجَد أي حل
    حتى بعد استبعاد كل المواد الممكنة أو نفاد الوقت كليًا.
    """
    n = len(course_codes)
    # ترتيب المواد بحسب موضعها الأصلي (لتفضيل استبعاد الأحدث عند التعادل)
    order_index = {code: i for i, code in enumerate(course_codes)}

    for num_to_exclude in range(0, n):
        if _time.time() > deadline:
            return None, None

        # نولّد تركيبات الاستبعاد بحجم num_to_exclude، مرتَّبة بحيث تُجرَّب
        # أولًا التركيبات التي تستبعد المواد الأحدث (الأكبر في order_index).
        candidates = list(combinations(course_codes, num_to_exclude))
        candidates.sort(key=lambda combo: sorted((order_index[c] for c in combo), reverse=True))

        for to_exclude in candidates:
            if _time.time() > deadline:
                return None, None
            remaining = [c for c in course_codes if c not in to_exclude]
            if not remaining:
                continue
            trial_vars = _variables_for_courses(variables, remaining)
            per_try_budget = max(0.15, deadline - _time.time())
            found, state = _has_any_full_solution(trial_vars, per_try_budget)
            if found:
                return set(remaining), state

    return None, None


def find_best_schedules(years_data, selected_list, get_course_fn, time_to_minutes_fn,
                         top_n=3, time_budget_seconds=4.0):
    """
    الواجهة العامة الرئيسية لهذه الوحدة.

    تُعيد قاموسًا بالشكل:
        {
            "schedules": [ {score, days_count, total_gap_minutes,
                             earliness_score, options: [SectionOption, ...]}, ... ],
            "excluded_courses": [ {"name": ..., "reason": ...}, ... ],
            "no_data_courses": [ "اسم مادة بدون معلومات وقت", ... ],
            "timed_out": bool,
        }

    إن لم يوجد أي حل كامل لكل المواد معًا، يُبحَث عن أكبر مجموعة فرعية
    فعلية ممكن جدولتها معًا بدون أي تعارض (انظر _find_max_feasible_subset)،
    لا مجرد استثناء جشع لمادة واحدة في كل مرة.
    """
    variables, no_data_courses = build_variables(years_data, selected_list, get_course_fn)
    _annotate_durations(variables, time_to_minutes_fn)

    if not variables:
        return {
            "schedules": [],
            "excluded_courses": [],
            "no_data_courses": no_data_courses,
            "timed_out": False,
        }

    overall_deadline = _time.time() + time_budget_seconds
    all_course_codes = _course_keys(variables)

    # محاولة أولى وأهم: كل المواد معًا. هذا يغطي الحالة الشائعة (لا تعارض
    # إطلاقًا) دون أي تكلفة بحث إضافية، قبل الانتقال لمسار الاستبعاد.
    full_found, full_state = _has_any_full_solution(variables, max(0.5, overall_deadline - _time.time()))

    if full_found:
        kept_codes = set(all_course_codes)
        state = full_state
        excluded = []
    elif full_state.timed_out:
        # انتهى الوقت قبل حتى تحديد إمكانية وجود حل كامل -- لا نملك وقتًا
        # كافيًا لمسار الاستبعاد المتدرّج، نعيد ما توصّلنا إليه بصدق.
        return {
            "schedules": [],
            "excluded_courses": [],
            "no_data_courses": no_data_courses,
            "timed_out": True,
        }
    else:
        kept_codes, state = _find_max_feasible_subset(
            variables, all_course_codes, overall_deadline
        )
        if kept_codes is None:
            # لم يُوجَد أي حل حتى لمادة واحدة فقط (نادر جدًا)، أو نفاد الوقت
            # الكلي أثناء البحث عن أكبر مجموعة ممكنة.
            return {
                "schedules": [],
                "excluded_courses": [],
                "no_data_courses": no_data_courses,
                "timed_out": _time.time() > overall_deadline,
            }
        excluded = [
            {
                "name": _course_name_for(variables, code),
                "reason": "يتعارض وقتها حتمًا مع باقي المواد المختارة، ولا توجد أي تركيبة شُعب تسمح بجدولتها معًا",
            }
            for code in all_course_codes
            if code not in kept_codes
        ]

    if state is None:
        return {
            "schedules": [],
            "excluded_courses": excluded,
            "no_data_courses": no_data_courses,
            "timed_out": False,
        }

    # نعيد تشغيل البحث الكامل (مع top_n الفعلي المطلوب من المستخدم، لا
    # top_n=1 المستخدَم أثناء فحص الجدوى فقط) على المجموعة المُبقاة، إن لم
    # تكن قد استُخدمت بالفعل بـ top_n الصحيح في المحاولة الأولى.
    if top_n > 1:
        kept_vars = _variables_for_courses(variables, kept_codes)
        remaining_budget = max(0.3, overall_deadline - _time.time())
        state = _run_search(kept_vars, top_n=top_n, time_budget_seconds=remaining_budget)

    schedules = []
    for score, options in state.best:
        days_count, total_gap, earliness = score
        schedules.append({
            "score": score,
            "days_count": days_count,
            "total_gap_minutes": total_gap,
            "earliness_score": earliness,
            "options": options,
        })

    return {
        "schedules": schedules,
        "excluded_courses": excluded,
        "no_data_courses": no_data_courses,
        "timed_out": state.timed_out,
    }


def _course_name_for(variables, course_code):
    for v in variables:
        if v.course_code == course_code:
            return v.course_name
    return course_code
