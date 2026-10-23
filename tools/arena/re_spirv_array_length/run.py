"""Compile and check re-spirv on CPU; never build or launch the Godot editor."""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import time

from fixtures import assembly
from spirv_model import Program

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', required=True, type=Path, help='Directory containing spirv-as/val/dis.')
    parser.add_argument('--output', required=True, type=Path, help='Fresh owned output directory outside the engine checkout.')
    parser.add_argument('--cxx', default='cl' if os.name == 'nt' else 'c++')
    parser.add_argument('--optimizer-source', type=Path, default=REPO / 'thirdparty/re-spirv/re-spirv.cpp')
    args = parser.parse_args()
    output, tools, source = args.output.resolve(), args.tools.resolve(), args.optimizer_source.resolve()
    if output == REPO or REPO in output.parents or output.exists():
        parser.error('Use a fresh output directory outside the engine checkout.')
    output.mkdir(parents=True)
    temporary = output / 'tmp'
    temporary.mkdir()
    env = dict(os.environ, TEMP=str(temporary), TMP=str(temporary))
    record = {'passed': False, 'scope': 'CPU optimizer and independent SPIR-V validation; no GPU/FPS qualification.',
              'source_revision': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
              'optimizer_source': str(source), 'optimizer_source_sha256': sha(source),
              'inputs_sha256': {str(path.relative_to(REPO)): sha(path) for path in
                  [REPO / 'thirdparty/re-spirv/re-spirv.h', REPO / 'thirdparty/spirv-headers/include/spirv/unified1/spirv.h',
                   HERE / 'optimize.cpp', HERE / 'fixtures.py', HERE / 'spirv_model.py', HERE / 'run.py']},
              'commands': [], 'cases': []}

    def execute(label, command):
        started = time.time()
        result = subprocess.run([str(item) for item in command], cwd=output, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0)
        log = output / (label + '.log')
        log.write_bytes(result.stdout)
        record['commands'].append({'label': label, 'command': list(map(str, command)), 'exit_code': result.returncode,
                                   'elapsed_seconds': time.time() - started, 'log_sha256': sha(log)})
        if result.returncode:
            raise RuntimeError(label + f' failed ({result.returncode}); inspect {log}')
        return result.stdout.decode('utf-8', errors='replace')

    try:
        suffix = '.exe' if os.name == 'nt' else ''
        tool_paths = {name: tools / (name + suffix) for name in ('spirv-as', 'spirv-val', 'spirv-dis')}
        record['tools'] = {name: {'sha256': sha(path), 'version': execute(name + '-version', [path, '--version'])}
                           for name, path in tool_paths.items()}
        binary = output / ('optimize' + suffix)
        includes = [REPO, REPO / 'thirdparty/spirv-headers/include', REPO / 'thirdparty/re-spirv']
        if os.name == 'nt':
            for label, path in [('optimizer', source), ('adapter', HERE / 'optimize.cpp')]:
                execute('compile-' + label, [args.cxx, '/nologo', '/std:c++17', '/O2', '/EHsc', '/MT',
                    *['/I' + str(path) for path in includes], '/c', path, '/Fo' + str(output / (label + '.obj'))])
            execute('link', [args.cxx, '/nologo', '/MT', output / 'optimizer.obj', output / 'adapter.obj',
                             '/Fe' + str(binary), '/link', '/INCREMENTAL:NO'])
        else:
            execute('compile', [args.cxx, '-std=c++17', '-O2', *['-I' + str(path) for path in includes],
                                source, HERE / 'optimize.cpp', '-o', binary])
        record['adapter_sha256'] = sha(binary)
        header = (REPO / 'thirdparty/spirv-headers/include/spirv/unified1/spirv.h').read_text(encoding='utf-8')
        opcodes = {int(value): name for name, value in re.findall(r'\bSpvOp(\w+)\s*=\s*(\d+)', header)}
        for kind in ('ordinary', 'length_only', 'dead_query', 'guarded_load'):
            asm, original = output / (kind + '.spvasm'), output / (kind + '.spv')
            asm.write_bytes(assembly(kind).encode())
            execute(kind + '-assemble', [tool_paths['spirv-as'], '--target-env', 'vulkan1.1', '--preserve-numeric-ids', asm, '-o', original])
            execute(kind + '-validate-input', [tool_paths['spirv-val'], '--target-env', 'vulkan1.1', original])
            before = Program(original.read_bytes(), opcodes)
            before.check_lengths()
            specifications = ('none', '0', '1') if kind == 'guarded_load' else ('none',)
            for inline, dce, specification in itertools.product((0, 1), (0, 1), specifications):
                label = f'{kind}-inline{inline}-dce{dce}-spec{specification}'
                case = {'name': label, 'passed': False}
                record['cases'].append(case)
                try:
                    optimized = output / (label + '.spv')
                    execute(label + '-optimize', [binary, original, optimized, inline, dce, specification])
                    execute(label + '-validate', [tool_paths['spirv-val'], '--target-env', 'vulkan1.1', optimized])
                    execute(label + '-disassemble', [tool_paths['spirv-dis'], optimized, '-o', output / (label + '.spvasm')])
                    after = Program(optimized.read_bytes(), opcodes)
                    members = after.check_lengths()
                    enabled = specification != '0'
                    if kind == 'length_only' or (kind == 'guarded_load' and (enabled or not dce)):
                        if sorted(members) != [0, 1]:
                            raise ValueError('Live runtime length queries or their member literals were lost.')
                    if dce and (kind == 'dead_query' or (kind == 'guarded_load' and not enabled)) and members:
                        raise ValueError('Dead runtime length query survived specialization/DCE.')
                    semantic_checks = 0
                    for length_a, length_b in itertools.product((0, 1, 4, 7), (0, 3, 5)):
                        for index in (0, 1, max(0, length_a - 1), length_a, 0xffffffff):
                            expected = (100 + index * 7 + length_b if index < length_a else 0) if kind == 'guarded_load' and enabled else length_a + length_b if kind == 'length_only' else 9
                            for program in (before, after):
                                if program.execute(index, length_a, length_b, enabled) != expected:
                                    raise ValueError('Observable output changed or a dynamic OOB guard was folded.')
                                semantic_checks += 1
                    case.update(passed=True, semantic_checks=semantic_checks, array_members=members,
                                output_sha256=sha(optimized), input_bytes=original.stat().st_size,
                                output_bytes=optimized.stat().st_size)
                except Exception as error:
                    case['failure'] = str(error)
        if len(record['cases']) != 24 or any(not case['passed'] for case in record['cases']):
            raise ValueError('One or more of the 24 optimizer contracts failed.')
        if sha(source) != record['optimizer_source_sha256']:
            raise ValueError('Optimizer source changed during testing.')
        for name, expected in record['inputs_sha256'].items():
            if sha(REPO / name) != expected:
                raise ValueError('Contract source changed during testing.')
        record['passed'] = True
    except Exception as error:
        record['failure'] = str(error)
    (output / 'receipt.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())
    passed = sum(case['passed'] for case in record['cases'])
    print(json.dumps({'passed': record['passed'], 'cases_passed': passed, 'cases': len(record['cases']),
                      'semantic_checks': sum(case.get('semantic_checks', 0) for case in record['cases']),
                      'receipt': str(output / 'receipt.json')}))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
