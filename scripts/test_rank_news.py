"""Synthetic ranking evidence; no live provider requests or personal data."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rank_news import WEIGHTS, rank_news


def evidence(values=None, **changes):
    return {
        "status": "ok", "provider": "google_trends", "scale": "web_0_100",
        "comparison_id": "synthetic-shared-export", "query": "Example product release",
        "query_kind": "term", "geo": "worldwide", "window_start": "2026-10-31",
        "window_end": "2026-11-01", "timezone": "UTC", "category": "all",
        "search_property": "web", "resolution": "daily",
        "source_url": "https://trends.google.com/trends/explore?test=synthetic",
        "collected_at": "2026-11-02T00:00:00Z", "attribution": "exact_event",
        "values": [40, 60] if values is None else values, "is_partial": False, **changes,
    }


def item(key="a", **changes):
    return {"event_id": key, "published_at": "2026-11-01", "verified": True,
            "scores": {"S": .75, "C": .5, "N": .75, "R": 1},
            "search_interest": evidence(), **changes}


def payload(*items):
    return {"as_of": "2026-11-01", "search_window": {"start": "2026-10-31", "end": "2026-11-01"},
            "items": list(items) or [item()]}


class RankNewsTests(unittest.TestCase):
    def test_shared_indices_change_order_and_preserve_attention_weight(self):
        a = item("a", search_interest=evidence([20, 20]))
        b = item("b", search_interest=evidence([80, 80], query="Another release"))
        result = rank_news(payload(a, b))
        self.assertEqual(["b", "a"], [r["event_id"] for r in result["ranked"]])
        self.assertAlmostEqual(result["ranked"][0]["score"] - result["ranked"][1]["score"], 4.5)
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1)
        self.assertAlmostEqual(WEIGHTS["C"] + WEIGHTS["E"], .15)
        self.assertEqual(result["ranked"][0]["search_heat"], 80)

    def test_separate_hundreds_and_context_mismatches_are_not_compared(self):
        for changes in ({"comparison_id": "another-export"}, {"geo": "US"},
                        {"timezone": "Asia/Shanghai"}, {"query_kind": "topic"},
                        {"category": "technology"}, {"search_property": "news"},
                        {"resolution": "hourly"}):
            with self.subTest(changes=changes):
                result = rank_news(payload(item("a", search_interest=evidence([100, 100])),
                                           item("b", search_interest=evidence([100, 100], **changes))))
                self.assertTrue(result["warnings"])
                self.assertTrue(all(r["search_status"] == "not_comparable" for r in result["ranked"]))
                self.assertTrue(all(r["search_heat"] is None for r in result["ranked"]))

    def test_wrong_or_stale_window_is_not_current_heat(self):
        row = rank_news(payload(item(search_interest=evidence(window_start="2026-09-01", window_end="2026-09-07"))))["ranked"][0]
        self.assertEqual(row["search_status"], "not_comparable")
        requested = payload(item(search_interest=evidence([50] * 7, window_start="2026-10-01", window_end="2026-10-07")))
        requested["search_window"] = {"start": "2026-10-01", "end": "2026-10-07"}
        self.assertEqual(rank_news(requested)["ranked"][0]["search_heat"], 50)

    def test_daily_window_requires_all_days(self):
        shortened = payload(item(search_interest=evidence([50])))
        row = rank_news(shortened)["ranked"][0]
        self.assertIsNone(row["search_heat"])
        self.assertEqual(row["search_status"], "not_comparable")
        default_window = payload(item(search_interest=evidence(window_start="2026-10-26")))
        del default_window["search_window"]
        self.assertEqual(rank_news(default_window)["ranked"][0]["search_status"], "not_comparable")
        default_window["items"][0]["search_interest"]["values"] = [50] * 7
        self.assertEqual(rank_news(default_window)["ranked"][0]["search_heat"], 50)

    def test_final_local_day_is_still_partial_even_if_declared_complete(self):
        for timestamp in ("2026-11-01T23:59:59Z", "2026-11-02T00:30:00+09:00"):
            with self.subTest(timestamp=timestamp):
                record = evidence(collected_at=timestamp, is_partial=False)
                row = rank_news(payload(item(search_interest=record)))["ranked"][0]
                self.assertIsNone(row["search_heat"])
                self.assertEqual(row["search_status"], "partial")
        complete = evidence(collected_at="2026-11-01T16:00:00Z", timezone="Asia/Shanghai")
        self.assertEqual(rank_news(payload(item(search_interest=complete)))["ranked"][0]["search_heat"], 50)

    def test_api_and_other_provider_scales_are_not_divided_by_100(self):
        for changes in ({"scale": "api_consistent_scale"}, {"provider": "baidu_index"}):
            row = rank_news(payload(item(search_interest=evidence([8000], **changes))))["ranked"][0]
            self.assertIsNone(row["search_heat"])
            self.assertEqual(row["search_status"], "unsupported_scale")

    def test_unavailable_partial_broad_and_small_samples_remain_unknown(self):
        cases = [(None, "not_configured"), (evidence([]), "insufficient_volume"),
                 (evidence([0, 0]), "insufficient_volume"), (evidence([None, 30]), "insufficient_volume"),
                 (evidence(is_partial=True), "partial"), (evidence(attribution="brand_background"), "ambiguous_query"),
                 ({"status": "auth_required"}, "auth_required"), ({"status": "rate_limited"}, "rate_limited"),
                 (evidence(collected_at="2026-10-25T00:00:00Z"), "partial"),
                 (evidence(collected_at="2026-11-02T00:00:00"), "not_comparable")]
        for record, expected in cases:
            with self.subTest(expected=expected, record=record):
                row = rank_news(payload(item(search_interest=record)))["ranked"][0]
                self.assertIsNone(row["search_heat"])
                self.assertEqual(row["search_status"], expected)

    def test_zero_points_are_disclosed_not_claimed_as_zero_queries(self):
        row = rank_news(payload(item(search_interest=evidence([0, 100]))))["ranked"][0]
        self.assertEqual(row["search_heat"], 50)
        self.assertEqual(row["reported_zero_points"], 1)

    def test_simulated_search_series_cannot_enter_formal_scoring(self):
        data = payload(item(search_interest=evidence([100, 100], data_kind="simulated")))
        simulated = rank_news(data)["ranked"][0]
        unavailable = rank_news(payload(item(search_interest=None)))["ranked"][0]
        self.assertIsNone(simulated["search_heat"])
        self.assertEqual(simulated["search_status"], "simulated_data")
        self.assertEqual(simulated["score"], unavailable["score"])
        self.assertEqual(simulated["possible_range"], unavailable["possible_range"])

    def test_observed_marker_preserves_results_and_unknown_marker_is_not_trusted(self):
        observed = rank_news(payload(item(search_interest=evidence(data_kind="observed"))))["ranked"][0]
        legacy = rank_news(payload(item()))["ranked"][0]
        self.assertEqual(observed, legacy)
        unknown = rank_news(payload(item(search_interest=evidence(data_kind="generated"))))["ranked"][0]
        self.assertIsNone(unknown["search_heat"])
        self.assertEqual(unknown["search_status"], "invalid_metadata")

    def test_missing_ranges_and_observed_community_zero(self):
        candidate = item(search_interest=None)
        row = rank_news(payload(candidate))["ranked"][0]
        self.assertAlmostEqual(row["possible_range"][1] - row["possible_range"][0], 7.5)
        candidate["scores"]["C"] = None
        row = rank_news(payload(candidate))["ranked"][0]
        self.assertIsNone(row["community_heat"])
        self.assertAlmostEqual(row["possible_range"][1] - row["possible_range"][0], 15)
        candidate["scores"]["C"] = 0
        zero = rank_news(payload(candidate))["ranked"][0]
        self.assertEqual(zero["community_heat"], 0)
        self.assertAlmostEqual(row["score"] - zero["score"], 3.75)

    def test_invalid_scores_values_dates_and_ids_are_rejected(self):
        mutations = [lambda p: p["items"][0]["scores"].update(S=True),
                     lambda p: p["items"][0]["scores"].update(N=float("nan")),
                     lambda p: p["items"][0]["scores"].update(R=1.1),
                     lambda p: p["items"][0]["scores"].update(E=1),
                     lambda p: p["items"][0]["search_interest"].update(values=[101]),
                     lambda p: p["items"][0]["search_interest"].update(values=[None, float("nan")]),
                     lambda p: p["items"][0]["search_interest"].update(values=[True]),
                     lambda p: p["items"][0].update(published_at="2026-11-02"),
                     lambda p: p["items"][0].update(verified=1),
                     lambda p: p["items"].append(copy.deepcopy(p["items"][0])),
                     lambda p: p.update(search_window={"start": "2026-11-01", "end": "2026-11-02"})]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = payload(); mutation(data)
                with self.assertRaises(ValueError): rank_news(data)

    def test_unverified_or_weak_evidence_cannot_win_by_heat(self):
        candidate = item("unverified", verified=False, search_interest=evidence([100]))
        weak = item("weak"); weak["scores"]["S"] = .25
        result = rank_news(payload(item(), candidate, weak))
        self.assertEqual(["a"], [x["event_id"] for x in result["ranked"]])
        self.assertEqual(2, len(result["excluded"]))

    def test_ties_and_input_are_stable(self):
        data = payload(item("b"), item("a")); original = copy.deepcopy(data)
        result = rank_news(data)
        self.assertEqual(result, rank_news({**data, "items": list(reversed(data["items"]))}))
        self.assertEqual(data, original)

    def test_cli_writes_results_without_overwriting_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "input.json", Path(temporary) / "output.json"
            source.write_text(json.dumps(payload()), encoding="utf-8")
            before = source.read_bytes()
            command = [sys.executable, str(Path(__file__).with_name("rank_news.py")), str(source)]
            subprocess.run(command + ["--output", str(output)], check=True, capture_output=True)
            self.assertEqual(json.loads(output.read_text()), rank_news(payload()))
            self.assertEqual(source.read_bytes(), before)
            blocked = subprocess.run(command + ["--output", str(source)], capture_output=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
