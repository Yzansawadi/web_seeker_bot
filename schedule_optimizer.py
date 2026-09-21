#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schedule_optimizer.py - optimized exact/anytime scheduler
-----------------------------------------------------------
Drop-in replacement for the original schedule_optimizer.py API.

Main improvements over the original engine:
1. Course-level bundling: theory/practical sections are combined first and
   internally conflicting combinations are removed. The search therefore
   has one variable per course instead of one variable per activity.
2. Bit-mask time representation for very fast overlap checks.
3. Dynamic MRV variable ordering (most constrained remaining course first),
   with a degree tie-breaker.
4. Forward checking after every assignment; incompatible domain values are
   removed immediately.
5. Strong day-count lower bound using an exact DP over the (normally <= 7)
   teaching days.
6. Lexicographic branch-and-bound on:
       fewer days -> fewer gaps -> earlier starts.
7. Exact maximal-subset search when no complete timetable exists. It optimizes
   the schedule among all subsets with the maximum possible number of courses,
   rather than returning the first feasible subset.
8. Optional anytime time limit. If time expires, the best solution found so far
   is returned honestly with timed_out=True. If the search finishes, the result
   is marked optimal_proven=True.

The public function find_best_schedules(...) keeps the original signature.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from itertools import product


# ---------------------------------------------------------------------------
# Basic data structures
# ---------------------------------------------------------------------------


@dataclass
class SectionOption:
    """One selectable section for one activity of one course."""

    course_code: str
    course_name: str
    activity: str
    section_id: str
    sessions: list = field(default_factory=list)


@dataclass
class Variable:
    """Compatibility structure retained for callers/tests of the old module."""

    course_code: str
    course_name: str
    activity: str
    options: list


@dataclass
class CourseBundle:
    """A complete internally-compatible choice for one course."""

    course_code: str
    course_name: str
    options: tuple
    sessions: tuple
    days_mask: int
    day_masks: tuple  # tuple[(day_index, bitmask), ...]
    earliest_by_day: tuple  # tuple[(day_index, start_min), ...]


# Canonical day order. Unknown day names are added after these.
_DAY_ALIASES = {
    "saturday": "Saturday",
    "السبت": "Saturday",
    "sunday": "Sunday",
    "الأحد": "Sunday",
    "الاحد": "Sunday",
    "monday": "Monday",
    "الاثنين": "Monday",
    "الإثنين": "Monday",
    "tuesday": "Tuesday",
    "الثلاثاء": "Tuesday",
    "wednesday": "Wednesday",
    "الأربعاء": "Wednesday",
    "الاربعاء": "Wednesday",
    "thursday": "Thursday",
    "الخميس": "Thursday",
    "friday": "Friday",
    "الجمعة": "Friday",
}

_DAY_ORDER = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _canonical_day(day):
    text = str(day).strip()
    return _DAY_ALIASES.get(text.lower(), text)


def _build_day_index(variables):
    seen = set()
    for var in variables:
        for opt in var.options:
            for s in opt.sessions:
                seen.add(_canonical_day(s["day"]))

    ordered = [d for d in _DAY_ORDER if d in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return {day: i for i, day in enumerate(ordered)}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _time_overlap(s1, e1, s2, e2):
    return s1 < e2 and s2 < e1


def _duration(session):
    start = session.get("start_min", 0)
    if "duration_min" in session:
        return max(0, int(session["duration_min"]))
    end = session.get("end_min")
    if end is not None:
        return max(0, int(end) - int(start))
    # Old data normally has duration_min added by _annotate_durations.
    return 60


def _annotate_durations(variables, time_to_minutes_fn):
    """Annotate session dicts once, matching the old module's behavior."""
    for var in variables:
        for opt in var.options:
            for s in opt.sessions:
                if "duration_min" not in s:
                    end_abs = time_to_minutes_fn(s["end"])
                    s["duration_min"] = max(0, end_abs - int(s["start_min"]))


# ---------------------------------------------------------------------------
# Build raw variables and course bundles
# ---------------------------------------------------------------------------


def build_variables(years_data, selected_list, get_course_fn):
    """Build the old-style (course × activity) variables."""
    variables = []
    no_data_courses = []

    for year, code in selected_list:
        course = get_course_fn(years_data, year, code)
        if course is None:
            continue
        if not course.get("sessions"):
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
                    sessions=list(sessions_list),
                )
                for section_id, sessions_list in sections.items()
            ]
            variables.append(
                Variable(
                    course_code=code,
                    course_name=course["name"],
                    activity=activity,
                    options=options,
                )
            )

    return variables, no_data_courses


def _section_day_masks(option, day_index):
    result = {}
    earliest = {}
    for s in option.sessions:
        day = _canonical_day(s["day"])
        idx = day_index[day]
        start = int(s["start_min"])
        duration = _duration(s)
        # Python arbitrary-precision integers make minute occupancy extremely cheap.
        if duration <= 0:
            continue
        bits = ((1 << duration) - 1) << start
        result[idx] = result.get(idx, 0) | bits
        earliest[idx] = min(earliest.get(idx, start), start)
    return result, earliest


def _merge_masks(a, b):
    for day_idx, mask in b.items():
        if a.get(day_idx, 0) & mask:
            return False
    for day_idx, mask in b.items():
        a[day_idx] = a.get(day_idx, 0) | mask
    return True


def _make_course_bundles(vars_for_course, day_index):
    """Cartesian-product the activities of one course with early conflict pruning."""
    # Most constrained activity first dramatically cuts intermediate products.
    activities = sorted(vars_for_course, key=lambda v: len(v.options))

    partials = [(tuple(), {}, {}, {})]  # (options, day_masks, earliest_by_day, metadata)

    for var in activities:
        prepared = []
        for opt in var.options:
            masks, earliest = _section_day_masks(opt, day_index)
            prepared.append((opt, masks, earliest))

        next_partials = []
        for chosen, used_masks, used_earliest, _meta in partials:
            for opt, opt_masks, opt_earliest in prepared:
                merged = dict(used_masks)
                if not _merge_masks(merged, opt_masks):
                    continue

                earliest2 = dict(used_earliest)
                for d, st in opt_earliest.items():
                    earliest2[d] = min(earliest2.get(d, st), st)

                next_partials.append((chosen + (opt,), merged, earliest2, {}))

        partials = next_partials
        if not partials:
            return []

    bundles = []
    seen = set()
    for chosen, masks, earliest, _meta in partials:
        # Collapse exactly identical occupied timetables for the same course.
        signature = tuple(sorted(masks.items()))
        if signature in seen:
            continue
        seen.add(signature)

        all_sessions = []
        for opt in chosen:
            all_sessions.extend(opt.sessions)

        days_mask = 0
        for d in masks:
            days_mask |= 1 << d

        bundles.append(
            CourseBundle(
                course_code=chosen[0].course_code,
                course_name=chosen[0].course_name,
                options=chosen,
                sessions=tuple(all_sessions),
                days_mask=days_mask,
                day_masks=tuple(sorted(masks.items())),
                earliest_by_day=tuple(sorted(earliest.items())),
            )
        )

    return bundles


def _group_variables_by_course(variables):
    grouped = {}
    order = []
    for var in variables:
        if var.course_code not in grouped:
            grouped[var.course_code] = []
            order.append(var.course_code)
        grouped[var.course_code].append(var)
    return [(code, grouped[code]) for code in order]


# ---------------------------------------------------------------------------
# Bundle conflict / score utilities
# ---------------------------------------------------------------------------


def _bundle_conflicts_busy(bundle, busy_by_day):
    for day_idx, mask in bundle.day_masks:
        if busy_by_day.get(day_idx, 0) & mask:
            return True
    return False


def _add_bundle(bundle, busy_by_day):
    for day_idx, mask in bundle.day_masks:
        busy_by_day[day_idx] = busy_by_day.get(day_idx, 0) | mask


def _remove_bundle(bundle, busy_by_day):
    # Safe because no two selected bundles overlap.
    for day_idx, mask in bundle.day_masks:
        old = busy_by_day.get(day_idx, 0)
        new = old ^ mask
        if new:
            busy_by_day[day_idx] = new
        else:
            busy_by_day.pop(day_idx, None)


def _compute_score(chosen_bundles):
    """Return the original public objective tuple: days, gaps, earliness."""
    by_day = {}
    for bundle in chosen_bundles:
        for s in bundle.sessions:
            by_day.setdefault(_canonical_day(s["day"]), []).append(s)

    days_count = len(by_day)
    total_gap = 0
    earliness = 0

    for sessions in by_day.values():
        ordered = sorted(sessions, key=lambda s: int(s["start_min"]))
        if not ordered:
            continue
        earliness += int(ordered[0]["start_min"])
        for i in range(1, len(ordered)):
            prev = ordered[i - 1]
            prev_end = int(prev["start_min"]) + _duration(prev)
            gap = int(ordered[i]["start_min"]) - prev_end
            if gap > 0:
                total_gap += gap

    return (days_count, total_gap, earliness)


# ---------------------------------------------------------------------------
# Lower bounds / exact DP over days
# ---------------------------------------------------------------------------


def _domain_day_masks(domains):
    return [tuple(sorted({b.days_mask for b in domain})) for domain in domains]


def _lower_bound_score(domains, busy_by_day):
    """Optimistic lexicographic bound for the full problem.

    There are normally only 7 teaching days, so we can exactly enumerate all
    reachable unions of day masks while deliberately ignoring time conflicts.
    This produces a strong lower bound on the minimum possible day count and a
    safe optimistic lower bound on earliness. Gap lower bound is always 0.
    """
    if not domains:
        days = len(busy_by_day)
        earliest = 0
        # Existing sessions determine the earliest start of each used day.
        for domain_day, _mask in busy_by_day.items():
            # The exact start is not recoverable from the occupancy mask alone
            # without scanning bits. Do that only for the <=7 days in the bound.
            bit = _lowest_set_bit_index(_mask)
            earliest += bit
        return (days, 0, earliest)

    reachable = {sum(1 << d for d in busy_by_day)}
    for domain in domains:
        day_masks = {b.days_mask for b in domain}
        new = set()
        for current in reachable:
            for dm in day_masks:
                new.add(current | dm)
        # At most 2^7 = 128 states in the normal university-week case.
        reachable = new
        if not reachable:
            return (10**9, 10**9, 10**9)

    # Earliest start per day: optimistic minimum over every still-possible bundle
    # plus the currently occupied schedule. Independent minima are deliberately
    # optimistic, hence valid for pruning.
    current_earliest = {}
    for day_idx, mask in busy_by_day.items():
        current_earliest[day_idx] = _lowest_set_bit_index(mask)

    possible_earliest = {}
    for domain in domains:
        for bundle in domain:
            for day_idx, start in bundle.earliest_by_day:
                possible_earliest[day_idx] = min(possible_earliest.get(day_idx, start), start)

    best_lb = None
    for final_mask in reachable:
        days = final_mask.bit_count()
        if best_lb is not None and days > best_lb[0]:
            continue
        early = 0
        for day_idx in range(final_mask.bit_length()):
            if not (final_mask & (1 << day_idx)):
                continue
            if day_idx in current_earliest:
                early += current_earliest[day_idx]
            else:
                # If a day is introduced only by a future bundle, an optimistic
                # lower bound is the smallest start of any bundle that can use it.
                early += possible_earliest.get(day_idx, 0)
        candidate = (days, 0, early)
        if best_lb is None or candidate < best_lb:
            best_lb = candidate

    return best_lb if best_lb is not None else (0, 0, 0)


def _lowest_set_bit_index(mask):
    if mask == 0:
        return 0
    low = mask & -mask
    return low.bit_length() - 1


def _subset_day_lower_bound(domains, busy_by_day, need_more_courses):
    """Minimum possible final day count if exactly/at least N more courses must fit.

    This is a tiny DP because there are normally <=7 days. It ignores time
    conflicts, so it is a safe optimistic bound for the subset solver.
    """
    if need_more_courses <= 0:
        return len(busy_by_day)

    current_mask = 0
    for d in busy_by_day:
        current_mask |= 1 << d

    # dp[k] = reachable day masks after selecting k of the remaining courses.
    dp = {0: {current_mask}}
    for domain in domains:
        next_dp = {k: set(v) for k, v in dp.items()}
        option_masks = {b.days_mask for b in domain}
        for k, states in dp.items():
            nk = k + 1
            if nk > need_more_courses:
                continue
            target = next_dp.setdefault(nk, set())
            for state in states:
                for dm in option_masks:
                    target.add(state | dm)
        dp = next_dp

    candidates = []
    for k, states in dp.items():
        if k >= need_more_courses:
            candidates.extend(state.bit_count() for state in states)
    return min(candidates) if candidates else 10**9


# ---------------------------------------------------------------------------
# Search state
# ---------------------------------------------------------------------------


class _SearchState:
    def __init__(self, top_n, time_budget_seconds):
        self.top_n = max(1, int(top_n))
        self.deadline = _time.time() + max(0.01, float(time_budget_seconds))
        self.best = []  # [(score_tuple, assignment)]
        self.nodes_explored = 0
        self.timed_out = False

    @property
    def optimal_proven(self):
        return not self.timed_out

    def time_up(self):
        if _time.time() >= self.deadline:
            self.timed_out = True
            return True
        return False

    def worst_best_score(self):
        if len(self.best) < self.top_n:
            return None
        return self.best[-1][0]

    def consider(self, score, assignment):
        entry = (score, list(assignment))
        if len(self.best) < self.top_n:
            self.best.append(entry)
            self.best.sort(key=lambda x: x[0])
            return
        if score < self.best[-1][0]:
            self.best[-1] = entry
            self.best.sort(key=lambda x: x[0])


# ---------------------------------------------------------------------------
# Forward checking + dynamic MRV search
# ---------------------------------------------------------------------------


def _static_degrees(domains):
    n = len(domains)
    degrees = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            related = False
            for a in domains[i]:
                if related:
                    break
                for b in domains[j]:
                    if not _bundles_compatible(a, b):
                        related = True
                        break
            if related:
                degrees[i] += 1
                degrees[j] += 1
    return degrees


def _bundles_compatible(a, b):
    # Use the canonical day occupancy masks.
    ia = dict(a.day_masks)
    for day_idx, bmask in b.day_masks:
        if ia.get(day_idx, 0) & bmask:
            return False
    return True


def _select_mrv_variable(domains, assigned, busy_by_day, degrees):
    best_idx = None
    best_key = None
    for i, domain in enumerate(domains):
        if assigned[i]:
            continue
        legal_count = 0
        for b in domain:
            if not _bundle_conflicts_busy(b, busy_by_day):
                legal_count += 1
        if legal_count == 0:
            return i, 0
        # MRV, then higher degree, then smaller original domain.
        key = (legal_count, -degrees[i], len(domain))
        if best_key is None or key < best_key:
            best_key = key
            best_idx = i
    return best_idx, (best_key[0] if best_key else 0)


def _filter_domains(domains, selected_idx, selected_bundle, busy_by_day, assigned):
    """Forward-check all unassigned domains against the new busy schedule."""
    new_domains = list(domains)
    for j, domain in enumerate(domains):
        if assigned[j] or j == selected_idx:
            continue
        filtered = [b for b in domain if not _bundle_conflicts_busy(b, busy_by_day)]
        if not filtered:
            return None
        new_domains[j] = filtered
    return new_domains


def _branch_score_heuristic(bundle, busy_by_day):
    existing_days = set(busy_by_day)
    added_days = sum(1 for d, _ in bundle.day_masks if d not in existing_days)
    earliest = min((s["start_min"] for s in bundle.sessions), default=10**9)
    duration = sum(_duration(s) for s in bundle.sessions)
    return (added_days, earliest, -duration)


def _search_full(domains, state, assigned, assignment, busy_by_day, degrees):
    if state.time_up():
        return

    state.nodes_explored += 1

    unassigned_domains = [domains[i] for i, flag in enumerate(assigned) if not flag]
    if not unassigned_domains:
        score = _compute_score(assignment)
        state.consider(score, assignment)
        return

    worst = state.worst_best_score()
    if worst is not None:
        lower = _lower_bound_score(unassigned_domains, busy_by_day)
        if lower > worst:
            return

    idx, legal_count = _select_mrv_variable(domains, assigned, busy_by_day, degrees)
    if idx is None or legal_count == 0:
        return

    legal = [b for b in domains[idx] if not _bundle_conflicts_busy(b, busy_by_day)]
    legal.sort(key=lambda b: _branch_score_heuristic(b, busy_by_day))

    assigned[idx] = True
    try:
        for bundle in legal:
            if state.time_up():
                return
            if _bundle_conflicts_busy(bundle, busy_by_day):
                continue

            _add_bundle(bundle, busy_by_day)
            assignment.append(bundle)
            filtered = _filter_domains(domains, idx, bundle, busy_by_day, assigned)

            if filtered is not None:
                _search_full(filtered, state, assigned, assignment, busy_by_day, degrees)

            assignment.pop()
            _remove_bundle(bundle, busy_by_day)
    finally:
        assigned[idx] = False


def _run_search(bundled_courses, top_n, time_budget_seconds):
    """Run the optimized full-course search."""
    domains = [list(domain) for _code, _name, domain in bundled_courses]
    degrees = _static_degrees(domains)
    state = _SearchState(top_n=top_n, time_budget_seconds=time_budget_seconds)
    assigned = [False] * len(domains)
    _search_full(domains, state, assigned, [], {}, degrees)
    return state


# ---------------------------------------------------------------------------
# Maximal feasible subset search
# ---------------------------------------------------------------------------


def _selection_tiebreak(excluded_indices):
    """Prefer excluding later selected courses when all primary scores tie."""
    return tuple(sorted(excluded_indices, reverse=True))


class _SubsetState(_SearchState):
    def __init__(self, time_budget_seconds, course_count):
        super().__init__(top_n=1, time_budget_seconds=time_budget_seconds)
        self.course_count = course_count
        self.best_included = -1
        self.best_score = None
        self.best_assignment = None
        self.best_excluded = None

    def consider_subset(self, included_count, score, assignment, excluded):
        better = False
        if included_count > self.best_included:
            better = True
        elif included_count == self.best_included:
            if self.best_score is None or score < self.best_score:
                better = True
            elif score == self.best_score:
                old_key = _selection_tiebreak(self.best_excluded or set())
                new_key = _selection_tiebreak(excluded)
                if new_key < old_key:
                    better = True

        if better:
            self.best_included = included_count
            self.best_score = score
            self.best_assignment = list(assignment)
            self.best_excluded = set(excluded)


def _search_subset(
    domains,
    course_names,
    state,
    assigned,
    assignment,
    excluded,
    busy_by_day,
    degrees,
):
    if state.time_up():
        return

    state.nodes_explored += 1

    remaining_indices = [i for i, flag in enumerate(assigned) if not flag]
    included_count = len(assignment)
    optimistic_max = included_count + len(remaining_indices)
    if optimistic_max < state.best_included:
        return

    if not remaining_indices:
        score = _compute_score(assignment) if assignment else (0, 0, 0)
        state.consider_subset(included_count, score, assignment, excluded)
        return

    need_for_best = max(0, state.best_included - included_count)
    if state.best_included >= 0:
        remaining_domains = [domains[i] for i in remaining_indices]
        if need_for_best > 0:
            lb_days = _subset_day_lower_bound(remaining_domains, busy_by_day, need_for_best)
        else:
            lb_days = len(busy_by_day)

        # If even the optimistic day count is already worse than the best score
        # for the same achievable number of included courses, prune.
        if included_count + len(remaining_indices) == state.best_included and state.best_score is not None:
            if (lb_days, 0, 0) > state.best_score:
                return

    # Dynamic MRV including only currently includable bundles.
    idx = None
    idx_key = None
    for i in remaining_indices:
        legal = [b for b in domains[i] if not _bundle_conflicts_busy(b, busy_by_day)]
        legal_count = len(legal)
        # A course with no legal inclusion choice is immediately a forced exclude.
        key = (legal_count, -degrees[i], len(domains[i]))
        if idx_key is None or key < idx_key:
            idx_key = key
            idx = i

    legal = [b for b in domains[idx] if not _bundle_conflicts_busy(b, busy_by_day)]
    legal.sort(key=lambda b: _branch_score_heuristic(b, busy_by_day))

    assigned[idx] = True
    try:
        # Inclusion branches first: quickly find a high-cardinality incumbent.
        for bundle in legal:
            if state.time_up():
                return
            _add_bundle(bundle, busy_by_day)
            assignment.append(bundle)

            filtered = list(domains)
            ok = True
            for j, domain in enumerate(domains):
                if assigned[j] or j == idx:
                    continue
                fd = [b for b in domain if not _bundle_conflicts_busy(b, busy_by_day)]
                filtered[j] = fd
                # Empty domains may still be excluded in subset mode, so they do
                # not invalidate the branch.

            _search_subset(
                filtered,
                course_names,
                state,
                assigned,
                assignment,
                excluded,
                busy_by_day,
                degrees,
            )

            assignment.pop()
            _remove_bundle(bundle, busy_by_day)

        # Exclusion branch. It cannot improve the cardinality if all remaining
        # courses can still be included to match the current best.
        if state.best_included < included_count + len(remaining_indices):
            excluded.add(idx)
            _search_subset(
                domains,
                course_names,
                state,
                assigned,
                assignment,
                excluded,
                busy_by_day,
                degrees,
            )
            excluded.remove(idx)
        elif state.best_included <= included_count:
            # We still need to explore exclusion when it can change schedule
            # quality at the same cardinality only if we have already included
            # enough courses to match the incumbent.
            excluded.add(idx)
            _search_subset(
                domains,
                course_names,
                state,
                assigned,
                assignment,
                excluded,
                busy_by_day,
                degrees,
            )
            excluded.remove(idx)
    finally:
        assigned[idx] = False


# ---------------------------------------------------------------------------
# Public helpers / compatibility wrappers
# ---------------------------------------------------------------------------


def _course_keys_from_bundles(bundled_courses):
    return [code for code, _name, _domain in bundled_courses]


def _course_name_for_bundles(bundled_courses, course_code):
    for code, name, _domain in bundled_courses:
        if code == course_code:
            return name
    return course_code


def find_best_schedules(
    years_data,
    selected_list,
    get_course_fn,
    time_to_minutes_fn,
    top_n=3,
    time_budget_seconds=4.0,
):
    """Find the best timetable(s) while preserving the original API."""
    variables, no_data_courses = build_variables(years_data, selected_list, get_course_fn)
    if not variables:
        return {
            "schedules": [],
            "excluded_courses": [],
            "no_data_courses": no_data_courses,
            "timed_out": False,
            "optimal_proven": True,
            "nodes_explored": 0,
        }

    _annotate_durations(variables, time_to_minutes_fn)
    day_index = _build_day_index(variables)

    grouped = _group_variables_by_course(variables)
    bundled_courses = []
    for code, course_vars in grouped:
        bundles = _make_course_bundles(course_vars, day_index)
        if bundles:
            bundled_courses.append((code, course_vars[0].course_name, bundles))
        else:
            # If theory/practical combinations are internally impossible, the
            # course behaves like an impossible course in the maximal-subset pass.
            bundled_courses.append((code, course_vars[0].course_name, []))

    if not bundled_courses:
        return {
            "schedules": [],
            "excluded_courses": [],
            "no_data_courses": no_data_courses,
            "timed_out": False,
            "optimal_proven": True,
            "nodes_explored": 0,
        }

    deadline = _time.time() + max(0.01, float(time_budget_seconds))

    # Remove impossible courses from the full search immediately; the subset
    # engine will still account for them in excluded_courses.
    full_domains = [d for _code, _name, d in bundled_courses]
    full_possible = all(bool(d) for d in full_domains)

    state = None
    excluded_codes = set()

    if full_possible:
        remaining = max(0.01, deadline - _time.time())
        state = _run_search(bundled_courses, top_n=top_n, time_budget_seconds=remaining)
        if state.best:
            kept_courses = bundled_courses
        else:
            kept_courses = None
    else:
        kept_courses = None

    # If a complete schedule was found, it is already optimal when the search
    # finished; if time expired, it is the best found so far.
    if kept_courses is not None:
        schedules = []
        for score, bundles in state.best:
            flat_options = []
            for bundle in bundles:
                flat_options.extend(bundle.options)
            schedules.append(
                {
                    "score": score,
                    "days_count": score[0],
                    "total_gap_minutes": score[1],
                    "earliness_score": score[2],
                    "options": flat_options,
                }
            )

        return {
            "schedules": schedules,
            "excluded_courses": [],
            "no_data_courses": no_data_courses,
            "timed_out": state.timed_out,
            "optimal_proven": not state.timed_out,
            "nodes_explored": state.nodes_explored,
        }

    # No complete solution: optimize over all subsets, primarily maximizing
    # the number of included courses, then timetable quality.
    subset_domains = [list(d) for _code, _name, d in bundled_courses]
    subset_names = [name for _code, name, _d in bundled_courses]
    degrees = _static_degrees([d if d else [] for d in subset_domains])
    subset_state = _SubsetState(max(0.01, deadline - _time.time()), len(subset_domains))
    assigned = [False] * len(subset_domains)
    excluded_idx = set()
    _search_subset(
        subset_domains,
        subset_names,
        subset_state,
        assigned,
        [],
        excluded_idx,
        {},
        degrees,
    )

    if subset_state.best_assignment is None:
        return {
            "schedules": [],
            "excluded_courses": [],
            "no_data_courses": no_data_courses,
            "timed_out": subset_state.timed_out,
            "optimal_proven": not subset_state.timed_out,
            "nodes_explored": subset_state.nodes_explored,
        }

    bundles_solution = subset_state.best_assignment
    flat_options = []
    kept_codes = {b.course_code for b in bundles_solution}
    for bundle in bundles_solution:
        flat_options.extend(bundle.options)

    score = _compute_score(bundles_solution)
    schedules = [
        {
            "score": score,
            "days_count": score[0],
            "total_gap_minutes": score[1],
            "earliness_score": score[2],
            "options": flat_options,
        }
    ]

    excluded = []
    for code, name, domain in bundled_courses:
        if code not in kept_codes:
            reason = (
                "لم توجد تركيبة خالية من التعارض تشمل هذه المادة ضمن أكبر مجموعة "
                "ممكنة من المواد المختارة، وفق البحث الكامل المتاح"
            )
            if not domain:
                reason = "لا توجد تركيبة نظرية/عملية داخلية خالية من التعارض لهذه المادة"
            excluded.append({"name": name, "reason": reason})

    return {
        "schedules": schedules,
        "excluded_courses": excluded,
        "no_data_courses": no_data_courses,
        "timed_out": subset_state.timed_out,
        "optimal_proven": not subset_state.timed_out,
        "nodes_explored": subset_state.nodes_explored,
    }


# Backward-compatible names used by the original module in tests/tools.

def _course_keys(variables):
    seen = []
    for v in variables:
        if v.course_code not in seen:
            seen.append(v.course_code)
    return seen


def _course_name_for(variables, course_code):
    for v in variables:
        if v.course_code == course_code:
            return v.course_name
    return course_code


__all__ = [
    "SectionOption",
    "Variable",
    "find_best_schedules",
    "build_variables",
]
