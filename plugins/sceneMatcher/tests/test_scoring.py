#!/usr/bin/env python3
"""Offline tests: titles are cleaned before searching and scoring, and dates earn a bonus.

The names below are the shapes real scene releases use: studio, a YY.MM.DD date,
performers, title, then resolution, source and a trailing -GROUP.
"""

import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scene_matcher
from scene_matcher import (
    clean_title, date_bonus, extract_date, normalize_title, score_scene, title_similarity,
)

EP = "https://stashdb.org/graphql"
RELEASE = "Brazzers.24.01.15.Jane.Doe.Hot.Day.XXX.1080p.MP4-WRB"


class TestCleanTitle(unittest.TestCase):
    CASES = [
        # (raw name, cleaned)
        (RELEASE, "Brazzers Jane Doe Hot Day"),
        ("Brazzers.2024.01.15.Jane.Doe.Hot.Day.XXX.2160p.MP4-WRB", "Brazzers Jane Doe Hot Day"),
        # A name that has already lost its extension keeps its last short word
        ("Brazzers.Jane.Doe.Hot.Day", "Brazzers Jane Doe Hot Day"),
        # A real video extension at the very end still goes
        ("Brazzers.Jane.Doe.Hot.Day.mp4", "Brazzers Jane Doe Hot Day"),
        ("Brazzers.Jane.Doe.Hot.Day.MKV", "Brazzers Jane Doe Hot Day"),
        # A trailing -word is only a release group after a resolution or source tag
        ("jane-doe-hot-scene", "jane doe hot scene"),
        ("Jane Doe - Hot Scene", "Jane Doe Hot Scene"),
        ("Jane.Doe.Hot.Scene.720p.WEBRip.x264-GUSH", "Jane Doe Hot Scene"),
        ("Jane.Doe.Hot.Day.XXX.1080p.WEB-DL.H.264-GRP", "Jane Doe Hot Day"),
        ("Jane.Doe.Hot.Day.XXX.480p.x265-GUSH[XC]", "Jane Doe Hot Day"),
        # Other date shapes
        ("Jane_Doe_Hot_Day_2024-01-15_1080p", "Jane Doe Hot Day"),
        ("TeamSkeet.15.01.2024.Jane.Doe.Hot.Day", "TeamSkeet Jane Doe Hot Day"),
        ("Jane Doe - Hot Day [1080p]", "Jane Doe Hot Day"),
        ("Jane.Doe.Hot.Day.1.5GB", "Jane Doe Hot Day"),
        # "Sex" is a title word, not a release tag
        ("Sex.On.The.Beach.XXX.1080p.MP4-WRB", "Sex On The Beach"),
        # A number that is not a date stays
        ("Studio.Hot.Day.2.1080p", "Studio Hot Day 2"),
    ]

    def test_cases(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(clean_title(raw), expected)

    def test_empty(self):
        self.assertEqual(clean_title(""), "")
        self.assertEqual(clean_title(None), "")


class TestPhaseOneSearchTerm(unittest.TestCase):
    """The text search runs on the cleaned title, so the clean_title bugs reached the search."""

    def test_search_term_from_release_filename(self):
        box = {"name": "StashDB", "endpoint": EP, "api_key": "k"}
        scene = {"id": "1", "title": "", "date": None, "stash_ids": [], "performers": [],
                 "studio": None, "files": [{"basename": RELEASE + ".mp4", "duration": 1800}]}
        with mock.patch.object(scene_matcher, "get_stashbox_config", return_value=[box]), \
             mock.patch.object(scene_matcher, "get_local_scene", return_value=scene), \
             mock.patch.object(scene_matcher, "local_stash_ids", return_value=set()), \
             mock.patch.object(scene_matcher, "query_stashdb_by_text", return_value=[]) as q:
            out = scene_matcher.find_matches_fast("1", {})
        self.assertEqual(q.call_args_list[0][0][2], "Brazzers Jane Doe Hot Day")
        self.assertEqual(out["search_attributes"]["cleaned_title"], "Brazzers Jane Doe Hot Day")


class TestNormalizeTitle(unittest.TestCase):
    def test_underscore_splits(self):
        self.assertEqual(normalize_title("Jane_Doe_Hot_Day"), "jane doe hot day")

    def test_apostrophe_joins(self):
        # Release names drop apostrophes: "Kates.Kitchen" must match "Kate's Kitchen"
        self.assertEqual(normalize_title("Kate's Kitchen"), "kates kitchen")


class TestExtractDate(unittest.TestCase):
    CASES = [
        # YY.MM.DD, the scene-release convention: year first
        (RELEASE, "2024-01-15"),
        ("Brazzers.99.05.12.Jane.Doe.Classic", "1999-05-12"),
        # 4-digit year first
        ("Brazzers.2024.01.15.Jane.Doe.Hot.Day", "2024-01-15"),
        ("Jane Doe - Hot Day - 2024-01-15", "2024-01-15"),
        ("Jane_Doe_Hot_Day_2024_01_15", "2024-01-15"),
        ("Jane.Doe.20240115.Hot.Day", "2024-01-15"),
        # DD.MM.YYYY only when the day is over 12; MM.DD.YYYY when the second number is
        ("TeamSkeet.15.01.2024.Jane.Doe", "2024-01-15"),
        ("TeamSkeet.01.15.2024.Jane.Doe", "2024-01-15"),
        ("TeamSkeet.05.01.2024.Jane.Doe", None),  # 5 Jan or May 1: ambiguous
        ("TeamSkeet.07.07.2024.Jane.Doe", "2024-07-07"),  # both readings agree
        # Not dates
        ("Brazzers.24.13.01.Jane.Doe", None),  # month 13
        ("Brazzers.2024.02.30.Jane.Doe", None),  # no such day
        ("Jane.Doe.Hot.Day.XXX.1080p.MP4-WRB", None),
        ("Jane.Doe.Hot.Day.x264.10.11", None),
        ("", None),
        (None, None),
    ]

    def test_cases(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(extract_date(raw), expected)


class TestContainmentTitleScore(unittest.TestCase):
    """title_similarity(local, stash_box): F1 of the share of the stash-box title found
    locally and the share of the local content it covers, names and stop words removed.
    format_results always passes the local studio and performer names, so these do too."""

    def test_title_inside_cleaned_filename(self):
        self.assertGreaterEqual(title_similarity("Jane Doe Hot Day", "Hot Day", ignore_names=["Jane Doe"]), 0.9)
        # A local word the title does not account for now counts: partial band
        sim = title_similarity("Jane Doe Hot Day", "Hot Day")
        self.assertGreaterEqual(sim, 0.5)
        self.assertLess(sim, 0.9)

    def test_score_band_for_title_inside_filename(self):
        scene = {"title": "Hot Day", "studio": None, "performers": [], "duration": 1800}
        score, _, title_match, _ = score_scene(scene, set(), None, local_title="Jane Doe Hot Day",
                                               local_duration=1800, known_names=["Jane Doe"])
        self.assertEqual(score, 10)
        self.assertTrue(title_match)

    def test_shared_stop_word_scores_zero(self):
        self.assertEqual(title_similarity("The Maid", "The Tutor"), 0)
        self.assertEqual(title_similarity("Jane Doe In Bed", "In"), 0)

    def test_stop_words_do_not_count_against(self):
        self.assertGreaterEqual(title_similarity("Jane Doe Hot Day", "The Hot Day", ignore_names=["Jane Doe"]), 0.9)

    def test_known_names_removed(self):
        # Without removing "Jane Doe", half the stash-box title would be found locally
        local = "Brazzers Jane Doe Hot Day"
        self.assertEqual(title_similarity(local, "Jane Doe Gets Wild",
                                          ignore_names=["Brazzers", "Jane Doe"]), 0)
        self.assertEqual(title_similarity(local, "Jane Doe", ignore_names=["Jane Doe"]), 0)
        self.assertGreaterEqual(title_similarity(local, "Jane Doe Hot Day",
                                                 ignore_names=["Brazzers", "Jane Doe"]), 0.9)

    def test_candidate_names_removed(self):
        # A stash-box scene titled with its own performer's name proves nothing about ours
        scene = {"title": "Jane Doe", "studio": {"id": "s", "name": "Brazzers"},
                 "performers": [{"performer": {"id": "p", "name": "Jane Doe"}}], "duration": 1800}
        score, _, title_match, _ = score_scene(scene, set(), None,
                                               local_title="Brazzers Jane Doe Hot Day",
                                               local_duration=1800)
        self.assertFalse(title_match)
        self.assertEqual(score, 0)

    def test_short_title_guard(self):
        # A lone 1-2 character token turns up in many names; containment alone is not enough
        self.assertLess(title_similarity("Jane Doe Hot Day 2", "2"), 0.9)
        self.assertLess(title_similarity("Brazzers VR Jane Doe Hot Day", "The VR"), 0.9)
        # An exact title still counts
        self.assertEqual(title_similarity("2", "2"), 1.0)

    def test_fuzzy_threshold(self):
        # "adventrue" vs "adventure" is 0.78, above the 0.75 threshold
        sim = title_similarity("Jane Doe Beach Adventrue", "Beach Adventure", ignore_names=["Jane Doe"])
        self.assertGreater(sim, 0.8)
        self.assertLess(sim, 0.9)
        # "hot" vs "hat" is 0.67, below it
        self.assertEqual(title_similarity("Jane Doe Hat", "Hot"), 0)


class TestDateBonus(unittest.TestCase):
    CASES = [
        # (local, stash-box, bonus)
        ("2024-01-15", "2024-01-15", 3),
        ("2024-01-15", "2024-01-16", 1),  # time zones
        ("2024-01-15", "2024-01-14", 1),
        ("2024-01-31", "2024-02-01", 1),  # a day apart across a month end
        ("2024-01-15", "2024-01", 1),     # stash-box has only year-month
        ("2024-01-15", "2024", 0),        # year only earns nothing
        ("2024-01-15", "2024-01-20", 0),
        ("2024-01-15", "2023-01-15", 0),
        ("2024-01-15", "2024-02", 0),
        (None, "2024-01-15", 0),
        ("2024-01-15", None, 0),
        ("2024-01-15", "20x4", 0),
    ]

    def test_cases(self):
        for local, remote, bonus in self.CASES:
            with self.subTest(local=local, remote=remote):
                self.assertEqual(date_bonus(local, remote), bonus)

    def test_score_scene_adds_bonus_and_flags_it(self):
        scene = {"title": "Other", "studio": None, "performers": [], "duration": 1800,
                 "release_date": "2024-01-15"}
        result = score_scene(scene, set(), None, local_duration=1800, local_date="2024-01-15")
        self.assertEqual(result[0], 3)
        self.assertTrue(result.matches_date)
        # Still unpacks as the four values callers expect
        score, performers, title_match, duration_score = result
        self.assertEqual((score, performers, title_match, duration_score), (3, 0, False, 1.0))

        miss = score_scene({**scene, "release_date": "2023-06-01"}, set(), None,
                           local_duration=1800, local_date="2024-01-15")
        self.assertEqual(miss[0], 0)
        self.assertFalse(miss.matches_date)


def local_scene(**kw):
    scene = {
        "id": "1", "title": "", "date": None, "stash_ids": [],
        "files": [{"basename": RELEASE + ".mp4", "duration": 1800}],
        "performers": [{"name": "Jane Doe", "stash_ids": []}],
        "studio": {"name": "Brazzers", "stash_ids": []},
    }
    scene.update(kw)
    return scene


def candidate(i, title, date):
    return {"id": i, "title": title, "release_date": date, "duration": 1800, "images": [],
            "studio": {"id": "st", "name": "Brazzers"},
            "performers": [{"performer": {"id": "p1", "name": "Jane Doe"}}]}


class TestFormatResults(unittest.TestCase):
    def results_for(self, scene, candidates):
        box = {"name": "StashDB", "endpoint": EP, "api_key": "k"}
        with mock.patch.object(scene_matcher, "get_stashbox_config", return_value=[box]), \
             mock.patch.object(scene_matcher, "get_local_scene", return_value=scene):
            context, err = scene_matcher.get_scene_context("1", {})
        self.assertIsNone(err)
        return {r["stash_id"]: r for r in
                scene_matcher.format_results({c["id"]: c for c in candidates}, context, set())}

    def test_cleaned_title_names_and_date(self):
        out = self.results_for(local_scene(), [
            candidate("right", "Hot Day", "2024-01-15"),
            candidate("wrong", "Jane Doe Gets Wild", "2023-06-01"),
        ])
        self.assertTrue(out["right"]["matches_title"])
        self.assertTrue(out["right"]["matches_date"])
        self.assertEqual(out["right"]["score"], 13)
        self.assertFalse(out["wrong"]["matches_title"])
        self.assertFalse(out["wrong"]["matches_date"])
        self.assertEqual(out["wrong"]["score"], 0)

    def test_scene_date_before_filename(self):
        out = self.results_for(local_scene(date="2024-02-01"), [
            candidate("feb", "Other", "2024-02-01"),
            candidate("jan", "Else", "2024-01-15"),
        ])
        self.assertTrue(out["feb"]["matches_date"])
        self.assertFalse(out["jan"]["matches_date"])

    def test_local_title_is_cleaned_too(self):
        out = self.results_for(local_scene(title="Brazzers - Jane Doe - Hot Day [1080p]", files=[]),
                               [candidate("a", "Hot Day", None)])
        self.assertTrue(out["a"]["matches_title"])
        self.assertEqual(out["a"]["score"], 10 * 0.75)  # no local duration: neutral multiplier


class TestShortGenericTitle(unittest.TestCase):
    """A short, generic stash-box title contained in the local name must not tie or beat
    the full title: containment alone scores both 1.0."""

    CASES = [
        # (local title, right title, wrong title)
        ("Jane Doe - Stepsister Massage Surprise", "Stepsister Massage Surprise", "Massage"),
        ("Jane Doe - Hot Day At The Beach", "Hot Day At The Beach", "Hot Day"),
    ]

    def ranked(self, local_title, right, wrong):
        box = {"name": "StashDB", "endpoint": EP, "api_key": "k"}
        scene = local_scene(
            title=local_title, files=[{"basename": "x.mp4", "duration": 1800}],
            performers=[{"name": "Jane Doe", "stash_ids": [{"endpoint": EP, "stash_id": "p1"}]}],
            studio={"name": "Brazzers", "stash_ids": [{"endpoint": EP, "stash_id": "st"}]})
        # The wrong one is closer in duration, so the title has to decide
        cands = {"right": {**candidate("right", right, None), "duration": 1500},
                 "wrong": {**candidate("wrong", wrong, None), "duration": 1700}}
        with mock.patch.object(scene_matcher, "get_stashbox_config", return_value=[box]), \
             mock.patch.object(scene_matcher, "get_local_scene", return_value=scene):
            context, err = scene_matcher.get_scene_context("1", {})
        self.assertIsNone(err)
        return scene_matcher.format_results(cands, context, set())

    def test_right_title_ranks_first(self):
        for local_title, right, wrong in self.CASES:
            with self.subTest(local=local_title):
                results = self.ranked(local_title, right, wrong)
                self.assertEqual([r["stash_id"] for r in results], ["right", "wrong"],
                                 [(r["stash_id"], r["score"]) for r in results])
                self.assertGreater(results[0]["score"], results[1]["score"])

    def test_similarity_counts_what_the_title_leaves_out(self):
        names = ["Jane Doe"]
        local = "Jane Doe Stepsister Massage Surprise"
        self.assertEqual(title_similarity(local, "Stepsister Massage Surprise", ignore_names=names), 1.0)
        self.assertLess(title_similarity(local, "Massage", ignore_names=names), 0.9)
        self.assertLess(title_similarity("Jane Doe Hot Day At The Beach", "Hot Day", ignore_names=names), 0.9)
        # Still a partial title match, not nothing
        self.assertGreaterEqual(title_similarity(local, "Massage", ignore_names=names), 0.5)


class TestLocalSceneQuery(unittest.TestCase):
    def test_fetches_date(self):
        with mock.patch.object(scene_matcher, "stash_graphql", return_value={"findScene": None}) as g:
            scene_matcher.get_local_scene("1")
        self.assertRegex(g.call_args[0][0], re.compile(r"^\s*date\s*$", re.M))


if __name__ == "__main__":
    unittest.main()
