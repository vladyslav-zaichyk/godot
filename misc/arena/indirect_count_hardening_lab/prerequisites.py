"""Strict prerequisite receipts for the frozen E10b candidate; no engine launch."""
import hashlib
import json
from pathlib import Path
import re
import importlib.util
from types import SimpleNamespace
from validate_receipt import load_json

E10A_INPUTS = ('IndirectCountLab.csproj', 'project.godot', 'Main.tscn', 'IndirectCountContract.cs',
    'ContractErrorLogger.cs', 'DrawVertex.glslinc', 'DrawFragment.glslinc', 'Producer.glslinc')
E10B_INPUTS = ('CountedMultimeshLab.csproj', 'project.godot', 'Main.tscn', 'CountedMultimeshContract.cs',
    'ContractErrorLogger.cs', 'Consumer.gdshader', 'Producer.glslinc')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(value, message):
    if not value:
        raise ValueError(message)

def validate_e10a_gate(gate, expected, direct_project):
    """Validate a completed prerequisite against this candidate's shipped direct consumer."""
    files = {name: gate / name for name in ('run.json', 'acceptance.json', 'contract.json', 'shutdown.json')}
    require(all(path.is_file() for path in files.values()), 'Complete direct E10a receipt family required.')
    hashes = {name: sha(path) for name, path in files.items()}
    run, acceptance, raw, shutdown = (load_json(files[name])
        for name in ('run.json', 'acceptance.json', 'contract.json', 'shutdown.json'))
    require(acceptance.get('passed') is True and acceptance.get('gpu_executed') is True
            and acceptance.get('contract_sha256') == hashes['contract.json']
            and acceptance.get('shutdown_sha256') == hashes['shutdown.json']
            and shutdown.get('passed') is True and shutdown.get('diagnostics') == [], 'Direct E10a gate failed or receipt hashes differ.')
    supported = raw.get('supports_draw_indirect_count')
    require(raw.get('contract') == 'indirect-count-numeric-contract-v1' and raw.get('passed') is True
            and type(supported) is bool and raw.get('count_draw_qualified') is supported,
            'Positive direct E10a drawing or its separate unavailable guard is required before native execution.')
    require(run.get('gpu_requested') is True and [step.get('label') for step in run.get('commands', [])]
            == ['dotnet-version', 'restore', 'build', 'runtime'] and all(type(step.get('exit_code')) is int
            and step['exit_code'] == 0 for step in run['commands']), 'Direct E10a command ledger is incomplete.')
    for name in ('engine_sha256', 'engine_source_hash', 'managed_api_sha256', 'managed_release_api_sha256', 'sdk_version'):
        require(name in expected and run.get(name) == expected[name], 'Direct E10a gate uses a different candidate: ' + name)
    require(raw.get('engine_hash') == expected['engine_source_hash'] and raw.get('sdk_version') == expected['sdk_version']
            and raw.get('managed_api_assembly_sha256') == expected['managed_api_sha256'], 'Direct E10a runtime candidate differs.')
    require(all((direct_project / name).is_file() for name in E10A_INPUTS), 'Candidate direct E10a source files are missing.')
    wanted = {name: sha(direct_project / name) for name in E10A_INPUTS}
    for ledger in (run, acceptance, raw):
        source = ledger.get('source_sha256')
        require(type(source) is dict and set(source) == set(E10A_INPUTS)
                and all(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) for value in source.values())
                and source == wanted, 'Direct E10a source identity is missing or differs from the candidate.')
    fixture_hash = run.get('managed_fixture_assembly_sha256')
    require(type(fixture_hash) is str and re.fullmatch('[0-9a-f]{64}', fixture_hash)
            and raw.get('managed_fixture_assembly_sha256') == fixture_hash, 'Direct E10a fixture assembly identities differ.')
    project = gate / 'work/project'
    assembly = project / '.godot/mono/temp/bin/Debug/IndirectCountLab.dll'
    if assembly.exists():
        require(assembly.is_file() and sha(assembly) == fixture_hash, 'Direct E10a compiled fixture file differs.')
    if project.exists():
        require(all((project / name).is_file() and sha(project / name) == wanted[name] for name in E10A_INPUTS),
                'Direct E10a generated project differs from its candidate sources.')
    return files, hashes


def validate_prerequisites(candidate, expected, direct_output, native_output=None):
    """Validate positive same-candidate gates, including their independent oracles."""
    files, hashes = validate_e10a_gate(direct_output, expected, candidate / 'indirect_count_lab/project')
    direct = load_json(files['contract.json'])
    require(direct.get('supports_draw_indirect_count') is True and direct.get('count_draw_qualified') is True,
            'Hardening requires positive direct count drawing, not an unavailable guard.')
    groups = [('direct', direct_output, files, hashes, 'indirect_count_lab', direct)]
    if native_output is not None:
        native_files = {name: native_output / name for name in ('run.json', 'acceptance.json', 'contract.json', 'shutdown.json')}
        require(all(p.is_file() for p in native_files.values()), 'Complete native E10b receipt family required.')
        native_hashes = {name: sha(p) for name, p in native_files.items()}
        run, acceptance, raw, shutdown = (load_json(native_files[n]) for n in native_files)
        require(acceptance.get('passed') is True and acceptance.get('gpu_executed') is True
                and acceptance.get('contract_sha256') == native_hashes['contract.json']
                and acceptance.get('shutdown_sha256') == native_hashes['shutdown.json']
                and shutdown.get('passed') is True and shutdown.get('diagnostics') == [], 'Native E10b prerequisite failed.')
        require(raw.get('contract') == 'counted-multimesh-numeric-contract-v1'
                and raw.get('passed') is True and raw.get('native_count_qualified') is True,
                'Positive native E10b drawing prerequisite required.')
        for name in ('engine_sha256', 'engine_source_hash', 'managed_api_sha256', 'managed_release_api_sha256', 'sdk_version'):
            require(run.get(name) == expected[name], 'Native prerequisite candidate differs: ' + name)
        require(run.get('gpu_requested') is True and run.get('e10a_gate', {}).get('receipt_sha256') == hashes,
                'Native prerequisite is not linked to these direct E10a receipts.')
        wanted = {name: sha(candidate / 'counted_multimesh_lab/project' / name) for name in E10B_INPUTS}
        require(all(record.get('source_sha256') == wanted for record in (run, raw, acceptance)), 'Native prerequisite source differs.')
        require(raw.get('engine_hash') == expected['engine_source_hash'] and raw.get('sdk_version') == expected['sdk_version']
                and raw.get('managed_api_assembly_sha256') == expected['managed_api_sha256'], 'Native runtime identity differs.')
        assembly = native_output / 'work/project/.godot/mono/temp/bin/Debug/CountedMultimeshLab.dll'
        require(assembly.is_file() and sha(assembly) == run.get('managed_fixture_assembly_sha256')
                == raw.get('managed_fixture_assembly_sha256'), 'Native prerequisite compiled fixture differs.')
        groups.append(('native', native_output, native_files, native_hashes, 'counted_multimesh_lab', raw))
    result = {}
    for label, output, receipt_files, receipt_hashes, family, raw in groups:
        run = load_json(receipt_files['run.json'])
        require([s.get('label') for s in run.get('commands', [])] == ['dotnet-version', 'restore', 'build', 'runtime'],
                'Prerequisite command ledger differs.')
        for step in run['commands']:
            log = output / (step['label'] + '.log')
            require(type(step.get('exit_code')) is int and step['exit_code'] == 0
                    and log.is_file() and sha(log) == step.get('log_sha256'), 'Prerequisite command/log integrity differs.')
        validator = candidate / family / 'validate_receipt.py'
        spec = importlib.util.spec_from_file_location('hardening_prerequisite_' + label, validator)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate(raw, SimpleNamespace(engine_hash=expected['engine_source_hash'], sdk_version=expected['sdk_version']))
        result[label] = {'output': str(output), 'receipt_sha256': receipt_hashes}
    return result
