"""Adversarial CPU-only tests for the independent E10 receipt validator."""
from copy import deepcopy
import io
import unittest
from unittest.mock import patch

from validate_receipt import load_json, validate


class ReceiptValidation(unittest.TestCase):
    def setUp(self):
        self.oracle = {
            "contract": "e10-hardening-direct-oracle-v1", "assembly": "DirectHardeningLab",
            "inputs": ["Contract.cs", "oracle.json"],
            "not_qualified": ["No FPS or physical VRAM qualification"],
            "cases": {"alias": {
                "operations": ["begin", "count:0", "bind:reject", "draw:valid", "end", "readback"],
                "rasters": [{"probe": "final", "samples": [
                    {"x": 8, "y": 8, "expected_rgba": [255, 0, 0, 255]},
                    {"x": 24, "y": 8, "expected_rgba": [0, 255, 255, 255]}]}],
                "diagnostics": [{"kind": "error", "message": "Exact expected alias rejection.", "count": 1}],
                "resources": {"caller_owned_final": 0, "caller_requested_bytes_final": 0,
                              "local_owned_final": 0, "local_devices_final": 0}, "min_checks": 10}},
        }
        self.expected = {"engine_hash": "1" * 40, "sdk_version": "4.7.2-arena-dev-g00000.00000",
                         "managed_api_sha256": "2" * 64, "fixture_sha256": "3" * 64,
                         "source_sha256": {"Contract.cs": "4" * 64, "oracle.json": "5" * 64}}
        case = self.oracle["cases"]["alias"]
        self.raw = {"contract": "e10-hardening-direct-v1", "case_name": "alias", "passed": True,
                    "failure": "", "checks": 10, "engine_hash": self.expected["engine_hash"],
                    "sdk_version": self.expected["sdk_version"], "driver": "vulkan", "renderer": "forward_plus",
                    "gpu_name": "Synthetic CPU test GPU identity", "source_sha256": deepcopy(self.expected["source_sha256"]),
                    "managed_api_assembly_sha256": "2" * 64, "managed_fixture_assembly_sha256": "3" * 64,
                    "operations": deepcopy(case["operations"]),
                    "rasters": [{"probe": "final", "sha256": "6" * 64, "samples": [
                        {"x": 8, "y": 8, "actual_rgba": [255, 0, 0, 255]},
                        {"x": 24, "y": 8, "actual_rgba": [0, 255, 255, 255]}]}],
                    "expected_diagnostics": deepcopy(case["diagnostics"]),
                    "observed_diagnostics": [{"kind": "error", "message": "Exact expected alias rejection."}],
                    "resources": deepcopy(case["resources"])}

    def check_valid(self):
        self.assertIsNone(validate(self.raw, self.oracle, "alias", self.expected))

    def check_invalid(self):
        with self.assertRaises(ValueError):
            validate(self.raw, self.oracle, "alias", self.expected)

    def plateau(self, allowance=0):
        self.oracle["contract"] = "e10-hardening-native-oracle-v1"
        self.oracle["assembly"] = "NativeHardeningLab"
        self.raw["contract"] = "e10-hardening-native-v1"
        self.oracle["cases"]["alias"]["plateau"] = {
            "minimum_samples": 16, "warmup_cycles": 4, "allowed_growth_bytes": allowance}
        self.raw["resources"].update(warmup_cycles=4, measured_cycles=16,
                                     logical_buffer_samples=[100, 200, 150, 120] + [500] * 16)

    def test_positive_exact_contract(self):
        self.check_valid()

    def test_missing_every_required_root_field_rejects(self):
        original = deepcopy(self.raw)
        for key in original:
            with self.subTest(key=key):
                self.raw = deepcopy(original)
                del self.raw[key]
                self.check_invalid()

    def test_missing_every_case_field_rejects(self):
        original = deepcopy(self.oracle)
        for key in original["cases"]["alias"]:
            with self.subTest(key=key):
                self.oracle = deepcopy(original)
                del self.oracle["cases"]["alias"][key]
                self.check_invalid()

    def test_false_pass_and_nonempty_failure_reject(self):
        for key, value in (("passed", False), ("passed", 1), ("passed", "true"), ("failure", "failure"), ("failure", None)):
            with self.subTest(key=key, value=value):
                original = self.raw[key]
                self.raw[key] = value
                self.check_invalid()
                self.raw[key] = original

    def test_checks_reject_bool_float_negative_or_too_few(self):
        for value in (True, 10.0, -1, 9, "10"):
            with self.subTest(value=value):
                self.raw["checks"] = value
                self.check_invalid()

    def test_native_sdk_backend_gpu_and_contract_identity_reject(self):
        for key, value in (("engine_hash", "7" * 40), ("sdk_version", "different"), ("driver", "d3d12"),
                           ("renderer", "gl_compatibility"), ("gpu_name", " "),
                           ("contract", "e10-hardening-native-v1"), ("case_name", "other")):
            with self.subTest(key=key):
                original = self.raw[key]
                self.raw[key] = value
                self.check_invalid()
                self.raw[key] = original

    def test_api_and_fixture_assembly_changes_reject(self):
        for key in ("managed_api_assembly_sha256", "managed_fixture_assembly_sha256"):
            with self.subTest(key=key):
                original = self.raw[key]
                self.raw[key] = "7" * 64
                self.check_invalid()
                self.raw[key] = original

    def test_invalid_expected_digest_cannot_be_self_attested(self):
        for value in ("", "0" * 64, "z" * 64, "a" * 63, "A" * 64):
            with self.subTest(value=value):
                self.expected["fixture_sha256"] = value
                self.raw["managed_fixture_assembly_sha256"] = value
                self.check_invalid()

    def test_missing_extra_and_changed_source_input_reject(self):
        for mode in ("missing", "extra", "changed"):
            with self.subTest(mode=mode):
                self.raw["source_sha256"] = deepcopy(self.expected["source_sha256"])
                if mode == "missing":
                    del self.raw["source_sha256"]["Contract.cs"]
                elif mode == "extra":
                    self.raw["source_sha256"]["Unknown.cs"] = "7" * 64
                else:
                    self.raw["source_sha256"]["Contract.cs"] = "7" * 64
                self.check_invalid()

    def test_duplicate_or_unsafe_oracle_inputs_reject(self):
        for inputs in (["Contract.cs", "Contract.cs"], ["../Contract.cs", "oracle.json"],
                       ["D:/Contract.cs", "oracle.json"], ["x\\Contract.cs", "oracle.json"]):
            with self.subTest(inputs=inputs):
                self.oracle["inputs"] = inputs
                self.check_invalid()

    def test_operation_mutation_missing_duplicate_or_reorder_rejects(self):
        original = self.raw["operations"][:]
        for operations in (original[:-1], original + [original[-1]], list(reversed(original)),
                           ["fake"] + original[1:]):
            with self.subTest(operations=operations):
                self.raw["operations"] = operations
                self.check_invalid()

    def test_every_rgba_channel_and_every_pixel_is_checked(self):
        for sample in range(2):
            for channel in range(4):
                with self.subTest(sample=sample, channel=channel):
                    rgba = self.raw["rasters"][0]["samples"][sample]["actual_rgba"]
                    previous = rgba[channel]
                    rgba[channel] = previous - 2 if previous == 255 else previous + 2
                    self.check_invalid()
                    rgba[channel] = previous

    def test_single_channel_tolerance_one_is_allowed(self):
        self.raw["rasters"][0]["samples"][0]["actual_rgba"] = [254, 1, 0, 254]
        self.check_valid()

    def test_pixel_coordinates_order_and_sample_count_reject(self):
        original = deepcopy(self.raw["rasters"])
        for mode in ("x", "bool", "order", "missing", "extra"):
            with self.subTest(mode=mode):
                self.raw["rasters"] = deepcopy(original)
                samples = self.raw["rasters"][0]["samples"]
                if mode == "x":
                    samples[0]["x"] += 1
                elif mode == "bool":
                    samples[0]["x"] = True
                elif mode == "order":
                    samples.reverse()
                elif mode == "missing":
                    samples.pop()
                else:
                    samples.append(deepcopy(samples[0]))
                self.check_invalid()

    def test_rgba_type_shape_range_and_hash_reject(self):
        sample = self.raw["rasters"][0]["samples"][0]
        for rgba in ([True, 0, 0, 255], [255.0, 0, 0, 255], [255, 0, 0], [256, 0, 0, 255], [-1, 0, 0, 255]):
            with self.subTest(rgba=rgba):
                sample["actual_rgba"] = rgba
                self.check_invalid()
        sample["actual_rgba"] = [255, 0, 0, 255]
        self.raw["rasters"][0]["sha256"] = ""
        self.check_invalid()

    def test_missing_extra_duplicate_and_wrong_diagnostics_reject(self):
        valid = deepcopy(self.raw["observed_diagnostics"])
        for observed in ([], valid * 2, valid + [{"kind": "warning", "message": "Unexpected"}],
                         [{"kind": "warning", "message": valid[0]["message"]}],
                         [{"kind": "error", "message": "Partial expected alias rejection"}]):
            with self.subTest(observed=observed):
                self.raw["observed_diagnostics"] = observed
                self.check_invalid()

    def test_receipt_cannot_relax_oracle_diagnostic_count(self):
        self.raw["expected_diagnostics"][0]["count"] = 2
        self.raw["observed_diagnostics"] *= 2
        self.check_invalid()

    def test_zero_and_bool_diagnostic_counts_reject(self):
        for count in (0, True, 1.0, -1):
            with self.subTest(count=count):
                self.oracle["cases"]["alias"]["diagnostics"][0]["count"] = count
                self.raw["expected_diagnostics"][0]["count"] = count
                self.check_invalid()

    def test_every_resource_counter_must_be_exact_zero(self):
        for key in self.raw["resources"]:
            for value in (1, True, 0.0, -1):
                with self.subTest(key=key, value=value):
                    self.raw["resources"][key] = value
                    self.check_invalid()
                    self.raw["resources"][key] = 0

    def test_missing_or_extra_unreleased_resource_rejects(self):
        self.raw["resources"]["untracked_final"] = 1
        self.check_invalid()
        del self.raw["resources"]["untracked_final"]
        del self.raw["resources"]["local_devices_final"]
        self.check_invalid()

    def test_plateau_accepts_stable_tail_after_declared_warmup(self):
        self.plateau()
        self.check_valid()

    def test_plateau_rejects_too_few_or_inconsistent_cycles(self):
        for mode in ("few", "length", "warmup"):
            with self.subTest(mode=mode):
                self.setUp()
                self.plateau()
                if mode == "few":
                    self.raw["resources"]["measured_cycles"] = 15
                    self.raw["resources"]["logical_buffer_samples"].pop()
                elif mode == "length":
                    self.raw["resources"]["logical_buffer_samples"].pop()
                else:
                    self.raw["resources"]["warmup_cycles"] = 3
                self.check_invalid()

    def test_plateau_rejects_growth_and_late_decrease(self):
        for tail in ([500 + i for i in range(16)], [500] * 15 + [499]):
            with self.subTest(tail=tail):
                self.plateau()
                self.raw["resources"]["logical_buffer_samples"][4:] = tail
                self.check_invalid()

    def test_plateau_checks_positive_trend_not_only_range(self):
        self.plateau(allowance=10)
        self.raw["resources"]["logical_buffer_samples"][4:] = [500] * 8 + [510] * 8
        self.check_invalid()

    def test_plateau_rejects_bool_and_float_samples_and_unclaimed_data(self):
        for value in (True, 500.0, -1, 1 << 64):
            with self.subTest(value=value):
                self.plateau()
                self.raw["resources"]["logical_buffer_samples"][5] = value
                self.check_invalid()
        del self.oracle["cases"]["alias"]["plateau"]
        self.check_invalid()

    def test_nonplateau_allows_only_explicit_empty_counters(self):
        self.raw["resources"].update(logical_buffer_samples=[], warmup_cycles=0, measured_cycles=0)
        validate(self.raw, self.oracle, "alias", self.expected)

    def test_nonplateau_rejects_partial_nonzero_and_bool_counters(self):
        variants = ({"logical_buffer_samples": []},
                    {"logical_buffer_samples": [0], "warmup_cycles": 0, "measured_cycles": 0},
                    {"logical_buffer_samples": [], "warmup_cycles": False, "measured_cycles": 0},
                    {"logical_buffer_samples": [], "warmup_cycles": 0, "measured_cycles": 1})
        for fields in variants:
            with self.subTest(fields=fields):
                self.setUp()
                self.raw["resources"].update(fields)
                self.check_invalid()

    def test_unqualified_metadata_cannot_remove_exclusions(self):
        self.raw["not_qualified"] = []
        self.check_invalid()

    def test_failures_always_raise_valueerror_for_malformed_nesting(self):
        for key, value in (("rasters", None), ("resources", []), ("source_sha256", None),
                           ("expected_diagnostics", "error"), ("observed_diagnostics", [None])):
            with self.subTest(key=key):
                self.setUp()
                self.raw[key] = value
                self.check_invalid()

    def test_oracle_contract_mutation_and_unknown_requested_case_reject(self):
        for contract in ("e10-hardening-direct-v1", "e10-hardening-direct-oracle-v2", None, 1):
            with self.subTest(contract=contract):
                self.oracle["contract"] = contract
                self.check_invalid()
        self.setUp()
        with self.assertRaises(ValueError):
            validate(self.raw, self.oracle, "unknown", self.expected)

    def test_all_empty_or_mismatched_raster_probe_lists_reject(self):
        original = deepcopy(self.raw["rasters"])
        for rasters in ([], original * 2, [{**original[0], "probe": "other"}]):
            with self.subTest(rasters=rasters):
                self.raw["rasters"] = rasters
                self.check_invalid()

    def test_json_loader_rejects_duplicate_keys_and_nonfinite_values(self):
        for payload in (b'{"passed":false,"passed":true}', b'{"nested":{"x":1,"x":2}}',
                        b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}'):
            with self.subTest(payload=payload):
                with patch("builtins.open", return_value=io.BytesIO(payload)):
                    with self.assertRaises(ValueError):
                        load_json("unused-no-file-created")

    def test_json_loader_preserves_finite_metadata_without_writing_files(self):
        with patch("builtins.open", return_value=io.BytesIO(b'{"x":0.25,"n":2}')):
            self.assertEqual(load_json("unused-no-file-created"), {"x": 0.25, "n": 2})


if __name__ == "__main__":
    unittest.main()
