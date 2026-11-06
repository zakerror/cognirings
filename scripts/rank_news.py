#!/usr/bin/env python3
"""Rank curated news after research; no network access or upstream modifications."""
import argparse
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from statistics import mean
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


POLICY_VERSION = "cognirings-priority-v1.1.1"
WEIGHTS = {"S": .20, "T": .15, "C": .075, "E": .075, "N": .20, "R": .30}
MISSING_STATUSES = {
    "not_configured", "unavailable", "auth_required", "rate_limited",
    "insufficient_volume", "not_comparable", "ambiguous_query", "partial",
}
CONTEXT_FIELDS = (
    "comparison_id", "provider", "scale", "query_kind", "geo", "window_start",
    "window_end", "timezone", "category", "search_property", "resolution",
)


def number(value, ceiling=1):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= ceiling:
        raise ValueError(f"Expected a finite number between 0 and {ceiling}")
    return value


def day(value):
    if not isinstance(value, str):
        raise ValueError("Dates must use YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Dates must use YYYY-MM-DD")
    return parsed


def missing(reason):
    return {"value": None, "status": reason, "signature": None, "reported_zero_points": 0}


def search_score(record, window):
    """Only directly comparable Google Trends web series have a numeric adapter."""
    if record is None:
        return missing("not_configured")
    if not isinstance(record, dict):
        raise ValueError("search_interest must be an object or null")
    data_kind = record.get("data_kind", "observed")
    if data_kind == "simulated":
        return missing("simulated_data")
    if data_kind != "observed":
        return missing("invalid_metadata")
    status = record.get("status")
    if status in MISSING_STATUSES:
        return missing(status)
    if status != "ok":
        return missing("invalid_metadata")
    if record.get("provider") != "google_trends" or record.get("scale") != "web_0_100":
        return missing("unsupported_scale")
    if record.get("attribution") != "exact_event":
        return missing("ambiguous_query")
    if record.get("is_partial") is not False:
        return missing("partial")
    required = (*CONTEXT_FIELDS, "query", "source_url", "collected_at")
    if any(not isinstance(record.get(k), str) or not record[k].strip() for k in required):
        return missing("invalid_metadata")
    if record["query_kind"] not in {"term", "topic"}:
        return missing("invalid_metadata")
    source = urlsplit(record["source_url"])
    if source.scheme != "https" or source.hostname != "trends.google.com":
        return missing("invalid_metadata")
    try:
        start, end = day(record["window_start"]), day(record["window_end"])
        collected = datetime.fromisoformat(record["collected_at"].replace("Z", "+00:00"))
        timezone = ZoneInfo(record["timezone"])
    except (ValueError, ZoneInfoNotFoundError):
        return missing("invalid_metadata")
    if start > end or (start, end) != window or collected.tzinfo is None:
        return missing("not_comparable")
    # Date-bounded windows include the entire final local day.
    if collected.astimezone(timezone).date() <= end:
        return missing("partial")
    values = record.get("values")
    if not isinstance(values, list):
        return missing("invalid_metadata")
    # Check present observations even when another point is missing.
    for value in values:
        if value is not None:
            number(value, 100)
    if not values or any(x is None for x in values) or not any(values):
        return missing("insufficient_volume")
    if record["resolution"] == "daily" and len(values) != (end - start).days + 1:
        return missing("not_comparable")
    return {"value": mean(values) / 100, "status": "ok",
            "signature": tuple(record[k] for k in CONTEXT_FIELDS) + (len(values),),
            "reported_zero_points": values.count(0)}


def rank_news(data):
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Expected an object with an items array")
    as_of = day(data.get("as_of"))
    requested = data.get("search_window")
    if requested is None:
        window = (as_of - timedelta(days=6), as_of)
    else:
        if not isinstance(requested, dict):
            raise ValueError("search_window must contain start and end dates")
        window = day(requested.get("start")), day(requested.get("end"))
    if not window[0] <= window[1] <= as_of:
        raise ValueError("Search window must end on or before as_of")
    prepared, excluded, seen = [], [], set()
    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError("Each item must be an object")
        key = item.get("event_id")
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise ValueError("Event IDs must be nonempty and unique")
        seen.add(key)
        published = day(item.get("published_at"))
        if published > as_of:
            raise ValueError(f"Future publication date: {key}")
        if type(item.get("verified")) is not bool:
            raise ValueError("verified must be a boolean")
        scores = item.get("scores")
        if not isinstance(scores, dict) or set(scores) != {"S", "C", "N", "R"}:
            raise ValueError("scores must contain S, C, N, R; E is derived from evidence")
        for name in ("S", "N", "R"):
            number(scores[name])
        if scores["C"] is not None:
            number(scores["C"])
        if not item["verified"] or scores["S"] < .5:
            excluded.append({"event_id": key, "reason": "unverified_or_insufficient_evidence"})
            continue
        prepared.append((item, published, search_score(item.get("search_interest"), window)))
    signatures = {e["signature"] for _, _, e in prepared if e["value"] is not None}
    mixed = len(signatures) > 1
    ranked = []
    for item, published, engine in prepared:
        if mixed and engine["value"] is not None:
            engine = missing("not_comparable")
        scores = item["scores"]
        components = {**scores, "T": 2 ** (-(as_of - published).days / 7), "E": engine["value"]}
        missing_fields = [k for k in ("C", "E") if components[k] is None]
        imputed = {k: .5 if v is None else v for k, v in components.items()}
        contributions = {k: 100 * WEIGHTS[k] * imputed[k] for k in WEIGHTS}
        total = sum(contributions.values())
        missing_weight = sum(WEIGHTS[k] for k in missing_fields)
        row = {
            "event_id": item["event_id"], "components": components,
            "imputed_components": imputed, "contributions": contributions,
            "score": total, "display_score": round(total),
            "possible_range": [total - 50 * missing_weight, total + 50 * missing_weight],
            "missing_fields": missing_fields,
            "community_heat": None if scores["C"] is None else scores["C"] * 100,
            "search_heat": None if engine["value"] is None else engine["value"] * 100,
            "search_status": engine["status"],
            "reported_zero_points": engine["reported_zero_points"],
        }
        for field in ("title", "primary_node_id"):
            if field in item:
                row[field] = item[field]
        ranked.append(row)
    ranked.sort(key=lambda row: (-row["score"], row["event_id"]))
    for position, row in enumerate(ranked, 1):
        row["rank"] = position
    return {
        "policy_version": POLICY_VERSION, "weights": WEIGHTS.copy(), "as_of": str(as_of),
        "search_window": {"start": str(window[0]), "end": str(window[1])},
        "ranked": ranked, "excluded": excluded,
        "warnings": ["Search observations use different comparisons or contexts; E remains unknown."] if mixed else [],
        "interval_meaning": "Missing C/E sensitivity only, not a statistical confidence interval",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.input.resolve() == args.output.resolve():
        parser.error("Output must not overwrite the input")
    try:
        result = rank_news(json.loads(args.input.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
