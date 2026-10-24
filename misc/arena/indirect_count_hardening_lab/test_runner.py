"""Adversarial diagnostic and launch-gate tests. No Godot/dotnet subprocess."""
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_contract as runner


class RunnerSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.runner-unit-', dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'runtime.log'
        self.message = 'A counted indirect bank cannot be bound as a shader resource in the same draw list.'
        self.case = {'diagnostics': [{'kind': 'error', 'message': self.message, 'count': 1}]}

    def write_log(self, text):
        self.log.write_bytes(text.encode())

    def test_exact_expected_error_with_context_is_accepted(self):
        self.write_log('Godot test\nERROR: ' + self.message + '\n   at: bind (file.cpp:1)\n')
        self.assertTrue(runner.shutdown_diagnostics(self.log, self.case)['passed'])

    def test_repeated_expected_message_exceeds_budget(self):
        self.write_log(('ERROR: ' + self.message + '\n') * 2)
        with self.assertRaises(ValueError):
            runner.shutdown_diagnostics(self.log, self.case)

    def test_missing_expected_diagnostic_fails(self):
        self.write_log('Godot test\n')
        with self.assertRaises(ValueError):
            runner.shutdown_diagnostics(self.log, self.case)

    def test_warning_is_not_substitute_for_error(self):
        self.write_log('WARNING: ' + self.message + '\n')
        with self.assertRaises(ValueError):
            runner.shutdown_diagnostics(self.log, self.case)

    def test_expected_prefix_does_not_hide_other_error(self):
        self.write_log('ERROR: ' + self.message + ' Another failure.\n')
        with self.assertRaises(ValueError):
            runner.shutdown_diagnostics(self.log, self.case)

    def test_shutdown_leak_after_valid_expected_error_fails(self):
        self.write_log('ERROR: ' + self.message + '\nWARNING: ObjectDB instances leaked at exit.\n')
        with self.assertRaises(ValueError):
            runner.shutdown_diagnostics(self.log, self.case)

    def test_script_failure_is_not_whitelisted(self):
        self.write_log('ERROR: ' + self.message + '\nSCRIPT ERROR: Null reference.\n')
        with self.assertRaises(ValueError):
            runner.shutdown_diagnostics(self.log, self.case)

    def test_unhandled_runtime_failure_is_rejected(self):
        self.write_log('ERROR: ' + self.message + '\nUnhandled exception System.Exception\n')
        with self.assertRaises(ValueError):
            runner.shutdown_diagnostics(self.log, self.case)

    def test_clean_case_accepts_no_diagnostics(self):
        self.write_log('Godot test\n')
        self.assertTrue(runner.shutdown_diagnostics(self.log, {'diagnostics': []})['passed'])

    def test_clean_case_rejects_any_error(self):
        self.write_log('ERROR: ' + self.message + '\n')
        with self.assertRaises(ValueError):
            runner.shutdown_diagnostics(self.log, {'diagnostics': []})

    def test_duplicate_json_pass_flag_is_rejected(self):
        self.write_log('{"passed": false, "passed": true}')
        with self.assertRaises(ValueError):
            runner.read(self.log)

    def test_nonfinite_receipt_is_rejected(self):
        self.write_log('{"checks": NaN}')
        with self.assertRaises(ValueError):
            runner.read(self.log)

    def test_gpu_without_prerequisites_stops_before_candidate_or_launch(self):
        candidate = Path(self.temp.name) / 'unused-candidate'
        output = Path(self.temp.name).parents[4] / 'e10-hardening-parser-test-never-created'
        args = ['run_contract.py', '--candidate', str(candidate), '--output', str(output), '--run-gpu']
        error = StringIO()
        with patch.object(sys, 'argv', args), patch.object(runner, 'verify_candidate') as verify, patch.object(runner.subprocess, 'Popen') as launch:
            with redirect_stderr(error), self.assertRaises(SystemExit):
                runner.main()
            verify.assert_not_called()
            launch.assert_not_called()
        self.assertIn('--e10a-output', error.getvalue())

    def test_empty_suite_cannot_claim_full_matrix(self):
        direct = {'contract': 'e10-hardening-direct-oracle-v1', 'assembly': 'DirectHardeningLab',
                  'inputs': ['oracle.json', 'DirectHardeningLab.csproj'], 'cases': {'direct_case': {}}}
        native = {'contract': 'e10-hardening-native-oracle-v1', 'assembly': 'NativeHardeningLab',
                  'inputs': ['oracle.json', 'NativeHardeningLab.csproj'], 'cases': {}}
        with self.assertRaises(ValueError):
            runner.validate_oracles({'direct': direct, 'native': native}, {'direct': ['direct_case'], 'native': ['native_case']})

    def test_missing_case_cannot_shrink_full_coverage(self):
        direct = {'contract': 'e10-hardening-direct-oracle-v1', 'assembly': 'DirectHardeningLab',
                  'inputs': ['oracle.json', 'DirectHardeningLab.csproj'], 'cases': {'direct_case': {}}}
        with self.assertRaises(ValueError):
            runner.validate_oracles({'direct': direct}, {'direct': ['direct_case', 'omitted_case']})


if __name__ == '__main__':
    unittest.main()
