"""Independent exhaustive oracle and regression tests for timetable selection."""

import copy
import itertools
import random
import unittest
from unittest.mock import patch

import pandas as pd

import schedule_data as sd
import schedule_optimizer as opt


def meeting(day="Monday", start=480, end=540, activity="نظري", section="1", **extra):
    return {
        "day": day, "start_min": start, "end_min": end,
        "start": f"{start // 60:02}:{start % 60:02}" if isinstance(start, int) else start,
        "end": f"{end // 60:02}:{end % 60:02}" if isinstance(end, int) else end,
        "activity": activity, "section": section, "room": "", "teacher": "",
        **extra,
    }


def courses(*session_lists):
    return {1: {
        str(i): {"code": str(i), "name": f"Course {i}", "year": 1, "sessions": sessions}
        for i, sessions in enumerate(session_lists)
    }}


def solve(data, **kwargs):
    return opt.find_best_schedules(
        data, [(1, code) for code in data[1]], sd.get_course, sd.time_to_minutes,
        time_budget_seconds=kwargs.pop("time_budget_seconds", None), **kwargs,
    )


def independent_score(sessions):
    days = {}
    for session in sessions:
        days.setdefault(session["day"], []).append(session)
    gaps = 0
    earliest = 0
    for sessions_on_day in days.values():
        ordered = sorted(sessions_on_day, key=lambda s: s["start_min"])
        earliest += ordered[0]["start_min"]
        for left, right in zip(ordered, ordered[1:]):
            if left["end_min"] > right["start_min"]:
                return None
            gaps += right["start_min"] - left["end_min"]
    return len(days), gaps, earliest


def exhaustive_best(data):
    domains = []
    for course in data[1].values():
        activities = {}
        for session in course["sessions"]:
            activities.setdefault(session["activity"], {}).setdefault(session["section"], []).append(session)
        bundles = [
            [session for section in choice for session in section]
            for choice in itertools.product(*(activity.values() for activity in activities.values()))
        ]
        domains.append([None] + [bundle for bundle in bundles if independent_score(bundle) is not None])
    best = None
    for assignment in itertools.product(*domains):
        sessions = [session for bundle in assignment if bundle is not None for session in bundle]
        score = independent_score(sessions)
        if score is not None:
            objective = (-sum(bundle is not None for bundle in assignment), *score)
            if best is None or objective < best:
                best = objective
    return best


class OptimizerTests(unittest.TestCase):
    def assert_valid_result(self, result, data):
        for schedule in result["schedules"]:
            options = schedule["options"]
            sessions = [session for option in options for session in option.sessions]
            self.assertEqual(independent_score(sessions), schedule["score"])
            self.assertEqual(len(options), len({(o.course_code, o.activity) for o in options}))
            for code in {o.course_code for o in options}:
                expected = {s["activity"] for s in data[1][code]["sessions"]}
                self.assertEqual({o.activity for o in options if o.course_code == code}, expected)
                for option in (o for o in options if o.course_code == code):
                    original = [
                        s for s in data[1][code]["sessions"]
                        if s["activity"] == option.activity and s["section"] == option.section_id
                    ]
                    self.assertEqual(len(option.sessions), len(original))

    def test_matches_exhaustive_oracle_for_full_and_partial_schedules(self):
        rng = random.Random(7319)
        for case in range(150):
            session_lists = []
            for _ in range(rng.randint(1, 5)):
                sessions = []
                activities = ["نظري", "عملي"] if rng.random() < 0.6 else ["نظري"]
                for activity in activities:
                    for section in range(rng.randint(1, 2)):
                        start = rng.choice(range(480, 721, 30))
                        day = rng.choice(["Monday", "Tuesday", "Wednesday"])
                        sessions.append(meeting(
                            day, start, start + rng.choice([30, 60]), activity, str(section),
                        ))
                        if rng.random() < 0.15:
                            sessions.append(meeting(
                                "Thursday", 600, 660, activity, str(section),
                            ))
                session_lists.append(sessions)
            data = courses(*session_lists)
            with self.subTest(case=case):
                result = solve(data)
                self.assertTrue(result["optimal_proven"])
                self.assertFalse(result["timed_out"])
                self.assert_valid_result(result, data)
                if result["schedules"]:
                    best = result["schedules"][0]
                    objective = (-len({o.course_code for o in best["options"]}), *best["score"])
                else:
                    objective = (0, 0, 0, 0)
                self.assertEqual(objective, exhaustive_best(data))

    def test_days_take_priority_over_gaps(self):
        data = courses(
            [meeting()],
            [meeting(start=660, end=720), meeting("Tuesday", section="2")],
        )
        self.assertEqual(solve(data)["schedules"][0]["score"], (1, 120, 480))

    def test_gaps_take_priority_over_earlier_start(self):
        data = courses(
            [meeting(start=600, end=660)],
            [meeting(), meeting(start=540, end=600, section="2")],
        )
        self.assertEqual(solve(data)["schedules"][0]["score"], (1, 0, 540))

    def test_lower_bound_allows_future_earlier_meetings_and_filled_gaps(self):
        data = courses([meeting(start=540, end=600)])
        variables, _ = opt.build_variables(data, [(1, "0")], sd.get_course)
        opt._annotate_durations(variables, sd.time_to_minutes)
        domain = opt._make_course_bundles(variables, {"Monday": 0})
        busy = {0: ((1 << 60) - 1) << 600}
        self.assertEqual(opt._lower_bound_score([domain], busy), (1, 0, 540))
        busy[0] |= ((1 << 60) - 1) << 480
        self.assertEqual(opt._lower_bound_score([domain], busy), (1, 0, 480))

    def test_only_one_section_per_activity_with_all_its_meetings(self):
        data = courses([
            meeting(), meeting("Tuesday"), meeting(start=540, end=600, activity="عملي"),
            meeting("Wednesday", activity="عملي", section="2"),
        ])
        result = solve(data)
        self.assert_valid_result(result, data)
        self.assertEqual(len(result["schedules"][0]["options"]), 2)
        self.assertEqual(result["schedules"][0]["days_count"], 2)

    def test_overlapping_meetings_inside_one_section_are_rejected(self):
        result = solve(courses([meeting(), meeting(start=510, end=570)]))
        self.assertFalse(result["schedules"])
        self.assertEqual(len(result["excluded_courses"]), 1)

    def test_internal_theory_lab_conflict_does_not_drop_lab(self):
        result = solve(courses([meeting(), meeting(activity="عملي")]))
        self.assertFalse(result["schedules"])
        self.assertTrue(result["optimal_proven"])

    def test_duplicate_rows_and_selections_do_not_mutate_cached_data(self):
        data = courses([meeting(), meeting(), meeting(start=540, end=600, activity="عملي")])
        original = copy.deepcopy(data)
        result = opt.find_best_schedules(
            data, [(1, "0"), (1, "0")], sd.get_course, sd.time_to_minutes,
            time_budget_seconds=None,
        )
        self.assertEqual(len(result["schedules"][0]["options"]), 2)
        self.assertEqual(sum(len(o.sessions) for o in result["schedules"][0]["options"]), 2)
        self.assertEqual(data, original)
        self.assertEqual(result, solve(data))

    def test_invalid_times_never_become_zero_length_free_sessions(self):
        for invalid in ["", "invalid", "25:00", "8:99 AM", "8:00 AM trailing", "00:00 AM", "13:00 PM"]:
            with self.subTest(invalid=invalid):
                data = courses([meeting(end=invalid)], [meeting("Tuesday")])
                result = solve(data)
                self.assertEqual(result["no_data_courses"], ["Course 0"])
                self.assertEqual({o.course_code for o in result["schedules"][0]["options"]}, {"1"})

    def test_invalid_section_is_not_partially_retained(self):
        invalid = meeting("Tuesday", end="bad")
        data = courses([meeting(), invalid, meeting("Wednesday", section="2")])
        result = solve(data)
        self.assertEqual(result["schedules"][0]["options"][0].section_id, "2")
        self.assertFalse(result["no_data_courses"])

    def test_missing_activity_time_rejects_whole_course(self):
        result = solve(courses([meeting(), meeting(activity="عملي", end="")]))
        self.assertFalse(result["schedules"])
        self.assertEqual(result["no_data_courses"], ["Course 0"])

    def test_unknown_day_and_reversed_times_are_rejected(self):
        for session in [meeting("noday"), meeting(start=600, end=540), meeting(start=540, end=540)]:
            with self.subTest(session=session):
                result = solve(courses([session]))
                self.assertFalse(result["schedules"])
                self.assertEqual(result["no_data_courses"], ["Course 0"])

    def test_day_aliases_conflict(self):
        result = solve(courses([meeting("الأحد")], [meeting("sunday")]))
        self.assertEqual(len(result["excluded_courses"]), 1)

    def test_missing_selected_course_is_reported(self):
        result = opt.find_best_schedules(
            {}, [(1, "missing")], sd.get_course, sd.time_to_minutes,
        )
        self.assertEqual(result["no_data_courses"], ["missing"])
        self.assertFalse(result["schedules"])

    def test_near_alternative_is_not_hidden_by_distant_equal_score_solutions(self):
        data = courses([
            meeting(), meeting("Tuesday", section="2"), meeting("Wednesday", section="3"),
            meeting(start=490, end=550, section="4"), meeting(start=600, end=660, section="5"),
        ])
        result = solve(data, top_n=3)
        self.assertEqual([s["score"] for s in result["schedules"]], [(1, 0, 480), (1, 0, 490)])
        self.assertEqual(len(solve(data, top_n=1)["schedules"]), 1)

    def test_unique_primary_best_is_sent_alone(self):
        data = courses([meeting()], [
            meeting(start=540, end=600), meeting(start=550, end=610, section="2"),
        ])
        self.assertEqual(len(solve(data)["schedules"]), 1)

    def test_minor_shift_threshold_and_top_n(self):
        data = courses([
            meeting(start=480 + shift, end=540 + shift, section=str(shift))
            for shift in [0, 5, 10, 15, 16]
        ])
        self.assertEqual(len(solve(data, top_n=10)["schedules"]), 4)
        self.assertEqual(len(solve(data, top_n=3)["schedules"]), 3)

    def test_same_occupancy_different_activity_assignments_are_preserved(self):
        data = courses([
            meeting(start=480, end=540), meeting(start=540, end=600, section="2"),
            meeting(start=480, end=540, activity="عملي"),
            meeting(start=540, end=600, activity="عملي", section="2"),
        ])
        variables, _ = opt.build_variables(data, [(1, "0")], sd.get_course)
        opt._annotate_durations(variables, sd.time_to_minutes)
        bundles = opt._make_course_bundles(variables, {"Monday": 0})
        self.assertEqual(len(bundles), 2)
        self.assertEqual(len(solve(data)["schedules"]), 1)

    def test_identical_section_times_do_not_duplicate_output(self):
        data = courses([meeting(), meeting(section="2", teacher="Different teacher")])
        self.assertEqual(len(solve(data)["schedules"]), 1)

    def test_partial_schedule_is_best_over_all_maximum_subsets(self):
        data = courses(
            [meeting(start=480, end=600)],
            [meeting(start=480, end=540)],
            [meeting(start=540, end=600)],
        )
        result = solve(data)
        self.assertEqual({o.course_code for o in result["schedules"][0]["options"]}, {"1", "2"})
        self.assertEqual(result["excluded_courses"][0]["name"], "Course 0")

    def test_subset_tie_preserves_earlier_selection(self):
        result = solve(courses([meeting()], [meeting()]))
        self.assertEqual(result["schedules"][0]["options"][0].course_code, "0")

    def test_zero_budget_does_not_claim_infeasibility_or_start_subset_search(self):
        with patch.object(opt, "_search_subset") as subset:
            result = solve(courses([meeting()]), time_budget_seconds=0)
        subset.assert_not_called()
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["optimal_proven"])
        self.assertFalse(result["excluded_courses"])

    def test_timeout_after_incumbent_returns_only_unproven_best(self):
        clock = [0.0]
        original = opt._SearchState.consider

        def consider(state, score, assignment):
            original(state, score, assignment)
            clock[0] = 2.0

        data = courses([meeting(), meeting(start=490, end=550, section="2")])
        with patch.object(opt._time, "monotonic", side_effect=lambda: clock[0]):
            with patch.object(opt._SearchState, "consider", consider):
                result = solve(data, time_budget_seconds=1)
        self.assertEqual(len(result["schedules"]), 1)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["optimal_proven"])

    def test_deadline_covers_bundle_preparation(self):
        with patch.object(opt._time, "monotonic", side_effect=[0.0, 0.0, 2.0]):
            result = solve(courses([meeting()]), time_budget_seconds=1)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["schedules"])
        self.assertEqual(result["nodes_explored"], 0)

    def test_subset_timeout_does_not_claim_inevitable_exclusions(self):
        clock = [0.0]
        original = opt._SubsetState.consider_subset

        def consider(state, included_count, score, assignment, excluded):
            original(state, included_count, score, assignment, excluded)
            clock[0] = 2.0

        data = courses([meeting()], [meeting()], [meeting("Tuesday")])
        with patch.object(opt._time, "monotonic", side_effect=lambda: clock[0]):
            with patch.object(opt._SubsetState, "consider_subset", consider):
                result = solve(data, time_budget_seconds=1)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["optimal_proven"])
        self.assertIn("لم يثبت", result["excluded_courses"][0]["reason"])


class DataTests(unittest.TestCase):
    def test_time_formats_and_noon_midnight(self):
        for text, expected in {
            "8:00 AM": 480, "12:00 AM": 0, "12:00 PM": 720, "1:15 PM": 795,
            "23:59": 1439, "08:30:00": 510, "00:30": 30,
        }.items():
            self.assertEqual(sd.time_to_minutes(text), expected)

    def test_split_days_handles_missing_duplicates_and_aliases(self):
        self.assertEqual(sd._split_days(float("nan")), [])
        self.assertEqual(sd._split_days("الاحد / Sunday / الإثنين"), ["الأحد", "الاثنين"])

    def test_excel_loading_preserves_repeated_meetings_and_invalid_rows(self):
        subjects = pd.DataFrame([[1, 123, "Course"]])
        schedule = pd.DataFrame([
            [123, "Course", "نظري", "Monday / Tuesday", "8:00 AM", "9:00 AM", "", 1, ""],
            [123, "Course", "عملي", float("nan"), "9:00 AM", "10:00 AM", "", 1, ""],
        ])
        with patch.object(sd.pd, "read_excel", side_effect=[subjects, schedule]):
            with patch.object(sd.os.path, "exists", return_value=True):
                data = sd._load_courses_uncached()
        sessions = data[1]["123"]["sessions"]
        self.assertEqual(len(sessions), 3)
        self.assertEqual([s["day"] for s in sessions], ["الاثنين", "الثلاثاء", ""])
        self.assertEqual(sessions[0]["end_min"], 540)
        self.assertFalse(solve(data)["schedules"])


if __name__ == "__main__":
    unittest.main()
