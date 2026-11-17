"""Compare baseline and candidate frame-time measurements."""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


# These business thresholds are assumptions pending the stakeholder answers in PLAN.md.
WARMUP_END = 3
MIN_SAMPLES = 5
REGRESSION_MS = 2.0
REGRESSION_PERCENT = 10.0
REQUIRED_FIELDS = ("build", "scenario", "frame", "cpu_ms", "gpu_ms")


class AppError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise AppError("INVALID_ARGS", "명령행 인수가 올바르지 않습니다.")


def parse_args(argv):
    parser = JsonArgumentParser(add_help=False)
    parser.add_argument("--input", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--scenario")
    parser.add_argument("--status", choices=("regressed", "stable", "insufficient"))
    return parser.parse_args(argv)


def load_input(path):
    try:
        with Path(path).open(encoding="utf-8-sig") as source:
            document = json.load(source)
    except (UnicodeError, json.JSONDecodeError):
        raise AppError("INVALID_INPUT", "입력 파일이 유효한 UTF-8 JSON이 아닙니다.") from None
    except (OSError, ValueError):
        raise AppError("IO_ERROR", "입력 파일을 읽을 수 없습니다.") from None

    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or not isinstance(document.get("frames"), list)
    ):
        raise AppError("INVALID_INPUT", "지원하는 schema_version과 frames 배열이 필요합니다.")
    return document


def invalid_fields(row):
    if not isinstance(row, dict):
        return list(REQUIRED_FIELDS)

    invalid = []
    if row.get("build") not in ("baseline", "candidate"):
        invalid.append("build")
    scenario = row.get("scenario")
    try:
        valid_scenario = isinstance(scenario, str) and bool(scenario.strip()) and bool(scenario.encode("utf-8"))
    except UnicodeEncodeError:
        valid_scenario = False
    if not valid_scenario:
        invalid.append("scenario")
    if isinstance(row.get("frame"), bool) or not isinstance(row.get("frame"), int) or row["frame"] < 0:
        invalid.append("frame")
    for field in ("cpu_ms", "gpu_ms"):
        value = row.get(field)
        try:
            valid_number = (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
                and value >= 0
            )
        except (OverflowError, ValueError):
            valid_number = False
        if not valid_number:
            invalid.append(field)
    return invalid


def p95(values):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * 0.95
    low = math.floor(position)
    weight = position - low
    return ordered[low] + (ordered[math.ceil(position)] - ordered[low]) * weight


def make_item(scenario, groups):
    baseline = groups["baseline"]
    candidate = groups["candidate"]
    baseline_p95 = p95([frame[2] for frame in baseline])
    candidate_p95 = p95([frame[2] for frame in candidate])
    delta = None if baseline_p95 is None or candidate_p95 is None else candidate_p95 - baseline_p95
    delta_percent = None
    if delta is not None and baseline_p95 > 0:
        percentage = delta / baseline_p95 * 100
        if math.isfinite(percentage):
            delta_percent = percentage

    if len(baseline) < MIN_SAMPLES or len(candidate) < MIN_SAMPLES or delta_percent is None:
        status = "insufficient"
    elif delta >= REGRESSION_MS and delta_percent >= REGRESSION_PERCENT:
        status = "regressed"
    else:
        status = "stable"

    if status == "insufficient":
        bottleneck = "unknown"
    else:
        cpu_p95 = p95([frame[0] for frame in candidate])
        gpu_p95 = p95([frame[1] for frame in candidate])
        bottleneck = "balanced" if cpu_p95 == gpu_p95 else "cpu" if cpu_p95 > gpu_p95 else "gpu"

    return {
        "scenario": scenario,
        "baseline_count": len(baseline),
        "candidate_count": len(candidate),
        "baseline_p95_ms": baseline_p95,
        "candidate_p95_ms": candidate_p95,
        "delta_ms": delta,
        "delta_percent": delta_percent,
        "status": status,
        "bottleneck": bottleneck,
    }


def analyze(document, scenario_filter=None, status_filter=None):
    frames = document["frames"]
    groups = defaultdict(lambda: {"baseline": [], "candidate": []})
    scenarios = set()
    seen = set()
    warnings = []
    meta = {
        "total_rows": len(frames),
        "accepted_frames": 0,
        "ignored_warmup": 0,
        "invalid_rows": 0,
        "duplicate_rows": 0,
    }

    for row_number, row in enumerate(frames, start=1):
        fields = invalid_fields(row)
        if fields:
            meta["invalid_rows"] += 1
            warnings.append({"code": "INVALID_ROW", "row": row_number, "fields": fields})
            continue

        key = (row["build"], row["scenario"], row["frame"])
        if key in seen:
            meta["duplicate_rows"] += 1
            warnings.append(
                {"code": "DUPLICATE_FRAME", "row": row_number, "fields": ["build", "scenario", "frame"]}
            )
            continue
        seen.add(key)
        scenarios.add(row["scenario"])

        if row["frame"] < WARMUP_END:
            meta["ignored_warmup"] += 1
            continue

        groups[row["scenario"]][row["build"]].append(
            (float(row["cpu_ms"]), float(row["gpu_ms"]), float(max(row["cpu_ms"], row["gpu_ms"])))
        )
        meta["accepted_frames"] += 1

    items = [make_item(scenario, groups[scenario]) for scenario in scenarios]
    status_order = {"regressed": 0, "stable": 1, "insufficient": 2}
    items.sort(
        key=lambda item: (
            status_order[item["status"]],
            -(item["delta_percent"] if item["delta_percent"] is not None else -math.inf),
            item["scenario"],
        )
    )
    if scenario_filter is not None:
        items = [item for item in items if item["scenario"] == scenario_filter]
    if status_filter is not None:
        items = [item for item in items if item["status"] == status_filter]
    return {"items": items, "meta": meta, "warnings": warnings}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        result = analyze(load_input(args.input), args.scenario, args.status)
        exit_code = 0
    except AppError as error:
        result = {"error": {"code": error.code, "message": error.message}}
        exit_code = 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
