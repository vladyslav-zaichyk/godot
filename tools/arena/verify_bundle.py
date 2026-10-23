"""Verify every manifest-listed engine artifact without executing it."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    root = args.bundle.resolve()
    manifest = json.loads((root / 'manifest.json').read_bytes())
    files = manifest['artifacts_sha256']
    if not files:
        raise SystemExit('Empty artifact manifest.')
    for name, expected in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise SystemExit('Unsafe manifest path: ' + name)
        path = root.joinpath(*relative.parts)
        if not path.is_file() or path.resolve() != path.absolute():
            raise SystemExit('Missing or linked artifact: ' + name)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit('Artifact identity mismatch: ' + name)
    print(f'PASS: {len(files)} artifacts; native {manifest["native_source_commit"]}; SDK {manifest["sdk_version"]}')


if __name__ == '__main__':
    main()
