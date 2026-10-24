"""Validate E10 hardening correctness receipts; never launch an engine.

The runner supplies independently obtained binary/API/fixture/input identities.
Oracle operation order, pixel coordinates, resource constants and diagnostics
are authoritative. Extra descriptive receipt metadata does not qualify a case.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import PurePosixPath
import re


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _mapping(value, name):
    _require(type(value) is dict and all(type(k) is str for k in value), name + " must be an object")
    return value


def _sequence(value, name, nonempty=False):
    _require(type(value) is list and (not nonempty or bool(value)), name + " must be a list")
    return value


def _text(value, name):
    _require(type(value) is str and bool(value.strip()) and "\x00" not in value, name + " must be nonempty text")
    return value


def _integer(value, name, minimum=0):
    _require(type(value) is int and value >= minimum, name + " must be an integer >= " + str(minimum))
    return value


def _hash(value, name, length=64):
    _require(type(value) is str and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None
             and value != "0" * length, name + " must be a nonzero lowercase SHA" + str(length * 4))
    return value


def _strings(value, name, nonempty=False):
    for item in _sequence(value, name, nonempty):
        _text(item, name + " item")
    return value


def _input_names(value):
    names = _strings(value, "oracle inputs", True)
    _require(len(names) == len(set(names)), "duplicate oracle input")
    for name in names:
        relative = PurePosixPath(name)
        _require(not relative.is_absolute() and "\\" not in name and ":" not in name
                 and relative.as_posix() == name and ".." not in relative.parts,
                 "unsafe oracle input name")
    return names


def _source_hashes(value, names, label):
    mapping = _mapping(value, label)
    _require(set(mapping) == set(names), label + " input set differs")
    for name, digest in mapping.items():
        _hash(digest, label + " " + name)
    return mapping


def _diagnostics(value, label, with_counts):
    result = Counter()
    for item in _sequence(value, label):
        _mapping(item, label + " entry")
        fields = {"kind", "message", "count"} if with_counts else {"kind", "message"}
        _require(set(item) == fields, label + " entry fields differ")
        _require(item["kind"] in ("error", "warning"), label + " has an invalid kind")
        message = _text(item["message"], label + " message")
        key = item["kind"], message
        if with_counts:
            _require(key not in result, label + " has a duplicate expectation")
            result[key] = _integer(item["count"], label + " count", 1)
        else:
            result[key] += 1
    return result


def _rgba(value, name):
    _sequence(value, name)
    _require(len(value) == 4, name + " must contain four channels")
    for channel in value:
        _integer(channel, name + " channel")
        _require(channel <= 255, name + " channel exceeds 255")
    return value


def _rasters(actual, wanted):
    _sequence(actual, "receipt rasters", True)
    _sequence(wanted, "oracle rasters", True)
    _require(len(actual) == len(wanted), "raster probe count differs")
    seen = set()
    for index, (got, want) in enumerate(zip(actual, wanted)):
        _mapping(got, "receipt raster")
        _mapping(want, "oracle raster")
        probe = _text(want["probe"], "oracle probe")
        _require(probe not in seen, "duplicate oracle probe")
        seen.add(probe)
        _require(got["probe"] == probe, "raster probe order differs at " + str(index))
        _hash(got["sha256"], "raster full RGBA SHA256")
        observed = _sequence(got["samples"], "receipt samples", True)
        expected = _sequence(want["samples"], "oracle samples", True)
        _require(len(observed) == len(expected), "pixel sample count differs: " + probe)
        positions = set()
        for sample, target in zip(observed, expected):
            _mapping(sample, "receipt pixel")
            _mapping(target, "oracle pixel")
            x = _integer(target["x"], "oracle pixel x")
            y = _integer(target["y"], "oracle pixel y")
            _require((x, y) not in positions, "duplicate oracle pixel")
            positions.add((x, y))
            _require(type(sample["x"]) is int and type(sample["y"]) is int
                     and sample["x"] == x and sample["y"] == y, "pixel coordinates/order differ: " + probe)
            rgba = _rgba(sample["actual_rgba"], "actual RGBA")
            target_rgba = _rgba(target["expected_rgba"], "expected RGBA")
            _require(all(abs(a - b) <= 1 for a, b in zip(rgba, target_rgba)), "pixel differs: " + probe)


def _plateau(resources, config):
    _mapping(config, "oracle plateau")
    _require(set(config) == {"minimum_samples", "warmup_cycles", "allowed_growth_bytes"}, "plateau fields differ")
    minimum = _integer(config["minimum_samples"], "minimum plateau samples", 16)
    warmup = _integer(config["warmup_cycles"], "plateau warmup cycles", 4)
    allowance = _integer(config["allowed_growth_bytes"], "plateau byte allowance")
    _require(_integer(resources["warmup_cycles"], "observed warmup cycles") == warmup,
             "plateau warmup policy differs")
    measured = _integer(resources["measured_cycles"], "measured plateau cycles", minimum)
    samples = _sequence(resources["logical_buffer_samples"], "logical buffer samples", True)
    _require(len(samples) == warmup + measured, "plateau sample/cycle count differs")
    for sample in samples:
        _integer(sample, "logical buffer bytes")
        _require(sample <= (1 << 64) - 1, "logical buffer bytes exceed native uint64")
    tail = samples[warmup:]
    _require(max(tail) - min(tail) <= allowance, "logical buffer plateau range exceeds bound")
    # Check projected positive least-squares growth without float rounding or a
    # caller-reported pass flag. A late step can fail even within the range cap.
    count = len(tail)
    sx = count * (count - 1) // 2
    sxx = count * (count - 1) * (2 * count - 1) // 6
    numerator = count * sum(i * value for i, value in enumerate(tail)) - sx * sum(tail)
    denominator = count * sxx - sx * sx
    _require(max(0, numerator) * (count - 1) <= allowance * denominator,
             "logical buffer plateau positive trend exceeds bound")


def _resources(actual, wanted, plateau):
    _mapping(actual, "receipt resources")
    _mapping(wanted, "oracle resources")
    _require(bool(wanted), "oracle resource constants are empty")
    for name, value in wanted.items():
        _integer(value, "oracle resource " + name)
        _require(type(actual[name]) is int and actual[name] == value, "resource constant differs: " + name)
    finals = [name for name in actual if name.endswith("_final") or name.startswith("final_")]
    _require(bool(finals), "missing drained resource counters")
    for name in finals:
        _require(type(actual[name]) is int and actual[name] == 0, "resource not drained: " + name)
    if plateau is not None:
        _plateau(actual, plateau)
    else:
        fields = {"logical_buffer_samples", "warmup_cycles", "measured_cycles"}
        present = fields & set(actual)
        if present:
            _require(present == fields and type(actual["logical_buffer_samples"]) is list
                     and actual["logical_buffer_samples"] == []
                     and type(actual["warmup_cycles"]) is int and actual["warmup_cycles"] == 0
                     and type(actual["measured_cycles"]) is int and actual["measured_cycles"] == 0,
                     "unexpected unqualified plateau data")


def _validate(raw, oracle, case_name, expected):
    _mapping(raw, "receipt")
    _mapping(oracle, "oracle")
    _mapping(expected, "independent expected identity")
    _text(case_name, "case name")
    match = re.fullmatch(r"e10-hardening-(direct|native)-oracle-v1", _text(oracle["contract"], "oracle contract"))
    _require(match is not None, "unsupported oracle contract")
    _require(raw["contract"] == "e10-hardening-" + match.group(1) + "-v1", "receipt contract differs")
    _text(oracle["assembly"], "oracle assembly")
    _require(raw["case_name"] == case_name, "receipt case differs")
    cases = _mapping(oracle["cases"], "oracle cases")
    _require(case_name in cases, "unknown requested case")
    case = _mapping(cases[case_name], "oracle case")
    _require(raw["passed"] is True and type(raw["failure"]) is str and raw["failure"] == "", "receipt failed")
    _require(_integer(raw["checks"], "receipt checks") >= _integer(case["min_checks"], "oracle minimum checks", 1),
             "insufficient assertions")
    _hash(expected["engine_hash"], "expected engine SHA", 40)
    _text(expected["sdk_version"], "expected SDK version")
    _require(raw["engine_hash"] == expected["engine_hash"] and raw["sdk_version"] == expected["sdk_version"],
             "native/SDK identity differs")
    for key, field in (("managed_api_sha256", "managed_api_assembly_sha256"),
                       ("fixture_sha256", "managed_fixture_assembly_sha256")):
        _hash(expected[key], "expected " + key)
        _hash(raw[field], "receipt " + field)
        _require(raw[field] == expected[key], "managed identity differs: " + field)
    _require(raw["driver"] == "vulkan" and raw["renderer"] == "forward_plus", "wrong graphics backend")
    _text(raw["gpu_name"], "GPU name")
    names = _input_names(oracle["inputs"])
    sources = _source_hashes(raw["source_sha256"], names, "receipt sources")
    reference = _source_hashes(expected["source_sha256"], names, "independent sources")
    _require(sources == reference, "fixture source identity differs")
    operations = _strings(case["operations"], "oracle operations", True)
    _strings(raw["operations"], "receipt operations", True)
    _require(raw["operations"] == operations, "operation sequence differs")
    _rasters(raw["rasters"], case["rasters"])
    diagnostics = _diagnostics(case["diagnostics"], "oracle diagnostics", True)
    _require(_diagnostics(raw["expected_diagnostics"], "receipt expected diagnostics", True) == diagnostics
             and raw["expected_diagnostics"] == case["diagnostics"], "diagnostic expectation differs")
    _require(_diagnostics(raw["observed_diagnostics"], "observed diagnostics", False) == diagnostics,
             "observed diagnostic counts/content differ")
    _resources(raw["resources"], case["resources"], case.get("plateau"))
    if "not_qualified" in raw:
        _require(raw["not_qualified"] == _strings(oracle["not_qualified"], "oracle exclusions"),
                 "qualification exclusions differ")


def validate(raw, oracle, case_name, expected):
    """Return None on an exact contract match, otherwise raise ValueError.

The full-image digest is format-checked; complete image bytes are intentionally
not embedded in the receipt. Numerical acceptance uses every oracle sample.
"""
    try:
        _validate(raw, oracle, case_name, expected)
    except (KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValueError("Malformed receipt/oracle: " + str(error)) from error


def load_json(path):
    """Read JSON while rejecting duplicate keys and nonfinite numbers."""
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite JSON number: " + value)
    def floating(value):
        parsed = float(value)
        _require(math.isfinite(parsed), "nonfinite JSON number: " + value)
        return parsed
    with open(path, "rb") as stream:
        return json.load(stream, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
