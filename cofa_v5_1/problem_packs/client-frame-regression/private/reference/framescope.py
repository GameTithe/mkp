"""Reference implementation for an authored exercise, not a real profiler.
Pure analysis: no network, no writes, no raw records in diagnostics.
"""
from __future__ import annotations
from decimal import Decimal
from math import ceil
from typing import Any

NUMERIC = (int, float, Decimal)


def numeric(value: Any) -> bool:
    if type(value) not in NUMERIC:
        return False
    try:
        value = Decimal(str(value))
        return (value.is_finite() and Decimal(0) <= value <= Decimal(600000)
                and value == value.quantize(Decimal("0.000001")))
    except (ValueError, ArithmeticError):
        return False


def p95(values: list[Decimal]) -> Decimal | None:
    return sorted(values)[(95 * len(values) + 99) // 100 - 1] if values else None


def normalize(row: Any):
    if not isinstance(row, dict):
        return None, ['row']
    fields = []
    build = row.get('build')
    scenario = row.get('scenario')
    frame = row.get('frame')
    if not isinstance(build, str) or build.strip().lower() not in ('baseline', 'candidate'):
        fields.append('build')
    if not isinstance(scenario, str) or not scenario.strip():
        fields.append('scenario')
    if type(frame) is not int or frame < 0:
        fields.append('frame')
    for key in ('cpu_ms', 'gpu_ms'):
        if not numeric(row.get(key)):
            fields.append(key)
    if fields:
        return None, sorted(fields)
    return (build.strip().lower(), scenario.strip(), frame,
            Decimal(str(row['cpu_ms'])), Decimal(str(row['gpu_ms']))), []


def as_json_number(value):
    if value is None:
        return None
    # Valid frame inputs are bounded, so these outputs are finite.
    return float(value)


def analyze(document: Any, scenario_filter: str | None = None, status_filter: str | None = None) -> dict:
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1 or not isinstance(document.get('frames'), list)):
        raise ValueError('INVALID_INPUT')
    counters = dict(total_rows=len(document['frames']), accepted_frames=0,
                    ignored_warmup=0, invalid_rows=0, duplicate_rows=0)
    warnings, seen, groups = [], set(), {}
    for row_number, row in enumerate(document['frames'], 1):
        values, errors = normalize(row)
        if errors:
            counters['invalid_rows'] += 1
            warnings.append(dict(code='INVALID_ROW', row=row_number, fields=errors))
            continue
        build, scenario, frame, cpu, gpu = values
        key = (build, scenario, frame)
        if key in seen:
            counters['duplicate_rows'] += 1
            warnings.append(dict(code='DUPLICATE_FRAME', row=row_number, fields=[]))
            continue
        seen.add(key)
        group = groups.setdefault(scenario, {'baseline': [], 'candidate': []})
        if frame < 3:
            counters['ignored_warmup'] += 1
            continue
        counters['accepted_frames'] += 1
        group[build].append((cpu, gpu))
    items = []
    for scenario, group in groups.items():
        baseline, candidate = group['baseline'], group['candidate']
        old = p95([max(cpu, gpu) for cpu, gpu in baseline])
        new = p95([max(cpu, gpu) for cpu, gpu in candidate])
        delta = new - old if old is not None and new is not None else None
        percent = 100 * delta / old if delta is not None and old != 0 else None
        if len(baseline) < 5 or len(candidate) < 5:
            status = 'insufficient'
        else:
            # Cross multiplication avoids a rounded percentage at the decision boundary.
            regressed = delta >= 2 and (old == 0 or 10 * delta >= old)
            status = 'regressed' if regressed else 'stable'
        cpu95 = p95([cpu for cpu, gpu in candidate])
        gpu95 = p95([gpu for cpu, gpu in candidate])
        if not candidate:
            bottleneck = 'unknown'
        elif cpu95 - gpu95 >= 1:
            bottleneck = 'cpu'
        elif gpu95 - cpu95 >= 1:
            bottleneck = 'gpu'
        else:
            bottleneck = 'balanced'
        item = dict(scenario=scenario, baseline_count=len(baseline), candidate_count=len(candidate),
                    baseline_p95_ms=as_json_number(old), candidate_p95_ms=as_json_number(new),
                    delta_ms=as_json_number(delta), delta_percent=as_json_number(percent),
                    status=status, bottleneck=bottleneck)
        # Use unrounded values for ordering as well as classification.
        items.append((item, delta))
    order = {'regressed': 0, 'insufficient': 1, 'stable': 2}
    items.sort(key=lambda pair: (order[pair[0]['status']], pair[1] is None,
                                 -pair[1] if pair[1] is not None else 0, pair[0]['scenario']))
    selected = [item for item, delta in items
                if (scenario_filter is None or item['scenario'] == scenario_filter.strip())
                and (status_filter is None or item['status'] == status_filter)]
    return {'items': selected, 'meta': counters, 'warnings': warnings}
