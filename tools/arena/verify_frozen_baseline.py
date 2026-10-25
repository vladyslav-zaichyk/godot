#!/usr/bin/env python3
"""Read-only integrity check for the frozen Arena E13d/E13e Windows .NET bundle.

Usage: python verify_frozen_baseline.py <extracted-bundle-directory>
Uses only the Python standard library. Does not launch Godot, install packages,
write receipts, or modify the bundle. Extra files created by the editor are
allowed; every file declared by the original pinned manifest must stay intact.
Run against an idle extracted directory: this is an integrity check, not a
sandbox for a directory being modified concurrently by an adversary.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


NATIVE_COMMIT = "26f1b2b845817c7dfd3e7861cd04386a5f198df3"
SDK_VERSION = "4.7.2-arena-dev-g26f1b2b.845"
MANIFEST_SHA256 = "77b7448344888a409e5634592589ef305499a24eb85908626fb4747f6f3a58af"
EDITOR = "godot.windows.editor.x86_64.arena_dev.mono.exe"
PACKAGE_NAMES = ("Godot.NET.Sdk", "Godot.SourceGenerators", "GodotSharp", "GodotSharpEditor")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    prefix + str(number) for prefix in ("COM", "LPT") for number in range(1, 10)
}
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class VerificationError(Exception):
    def __init__(self, code, message, artifact=None):
        super().__init__(message)
        self.code = code
        self.artifact = artifact


def _fail(code, message, artifact=None):
    raise VerificationError(code, message, artifact)


def _plain_entry(path, *, directory, label):
    """Inspect without resolving links; reject all Windows reparse points."""
    try:
        info = path.lstat()
    except OSError as exc:
        _fail("missing_or_unreadable", str(exc), label)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE_POINT:
        _fail("linked_path", "Symbolic links and reparse points are not accepted.", label)
    correct_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct_kind:
        _fail("wrong_file_type", "Expected a regular directory." if directory else "Expected a regular file.", label)
    return info


def _bundle_root(bundle):
    # abspath normalizes user-supplied relative spelling without following links.
    root = Path(os.path.abspath(os.fspath(bundle)))
    for component in reversed((root, *root.parents)):
        _plain_entry(component, directory=True, label=str(component))
    return root


def _validate_artifact_path(relative):
    if not isinstance(relative, str) or not relative or len(relative) > 4096:
        _fail("unsafe_artifact_path", "Artifact path must be a short nonempty string.")
    if "\\" in relative or ":" in relative or any(ord(char) < 32 for char in relative):
        _fail("unsafe_artifact_path", "Artifact path contains a forbidden character.", relative)
    parts = relative.split("/")
    if PurePosixPath(relative).is_absolute() or any(part in ("", ".", "..") for part in parts):
        _fail("unsafe_artifact_path", "Artifact path must be a relative normalized path.", relative)
    for part in parts:
        if part.endswith((" ", ".")) or part.split(".", 1)[0].upper() in WINDOWS_DEVICES:
            _fail("unsafe_artifact_path", "Artifact path uses a Windows alias or device name.", relative)
    return parts


def _artifact_file(root, relative):
    parts = _validate_artifact_path(relative)
    current = root
    for index, part in enumerate(parts):
        current = current / part
        _plain_entry(current, directory=index < len(parts) - 1, label=relative)
    return current


def _hash_regular_file(path, label):
    before = _plain_entry(path, directory=False, label=label)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                _fail("file_changed", "File changed while being opened.", label)
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        _fail("missing_or_unreadable", str(exc), label)
    final = _plain_entry(path, directory=False, label=label)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(after) or identity(after) != identity(final):
        _fail("file_changed", "File changed during verification.", label)
    return digest.hexdigest(), after.st_size


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("invalid_manifest", "Duplicate JSON object key.")
        result[key] = value
    return result


def verify_bundle(bundle, *, expected_manifest_sha256=MANIFEST_SHA256,
                  expected_native=NATIVE_COMMIT, expected_sdk=SDK_VERSION):
    """Verify the bundle; optional pins exist for small synthetic CPU fixtures.

    The command-line entry point always uses the frozen release constants.
    """
    root = _bundle_root(bundle)
    manifest_path = _artifact_file(root, "manifest.json")
    actual_manifest_sha256, manifest_bytes = _hash_regular_file(manifest_path, "manifest.json")
    if actual_manifest_sha256 != expected_manifest_sha256:
        _fail("manifest_hash_mismatch", "Manifest does not match the frozen release.", "manifest.json")
    if manifest_bytes > 4 * 1024 * 1024:
        _fail("invalid_manifest", "Manifest exceeds the allowed size.", "manifest.json")
    try:
        manifest = json.loads(manifest_path.read_bytes(), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, ValueError) as exc:
        _fail("invalid_manifest", str(exc), "manifest.json")
    # A second digest closes accidental changes between hashing and parsing.
    if _hash_regular_file(manifest_path, "manifest.json")[0] != actual_manifest_sha256:
        _fail("file_changed", "Manifest changed during verification.", "manifest.json")
    if not isinstance(manifest, dict):
        _fail("invalid_manifest", "Manifest must be a JSON object.", "manifest.json")
    if manifest.get("native_source_commit") != expected_native:
        _fail("native_commit_mismatch", "Manifest identifies a different native commit.")
    if manifest.get("sdk_version") != expected_sdk:
        _fail("sdk_version_mismatch", "Manifest identifies a different SDK version.")
    artifacts = manifest.get("artifacts_sha256")
    if not isinstance(artifacts, dict) or not artifacts:
        _fail("invalid_manifest", "Manifest must declare artifact hashes.")
    normalized = set()
    for relative, expected_hash in artifacts.items():
        _validate_artifact_path(relative)
        if relative.casefold() in normalized:
            _fail("ambiguous_artifact_path", "Artifact paths collide on Windows.", relative)
        normalized.add(relative.casefold())
        if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
            _fail("invalid_manifest", "Invalid artifact SHA-256.", relative)
    required = (EDITOR, *(f"NuGetFeed/{name}.{expected_sdk}.nupkg" for name in PACKAGE_NAMES))
    for relative in required:
        if relative not in artifacts:
            _fail("required_artifact_missing", "Manifest omits the exact editor or SDK package.", relative)
    payload_bytes = 0
    for relative, expected_hash in artifacts.items():
        path = _artifact_file(root, relative)
        actual_hash, size = _hash_regular_file(path, relative)
        if actual_hash != expected_hash:
            _fail("artifact_hash_mismatch", "Artifact does not match the frozen manifest.", relative)
        payload_bytes += size
    return {
        "status": "verified",
        "native_source_commit": expected_native,
        "sdk_version": expected_sdk,
        "manifest_sha256": actual_manifest_sha256,
        "verified_artifact_count": len(artifacts),
        "verified_payload_bytes": payload_bytes,
        "extra_files_policy": "allowed; undeclared files are not verified",
        "executed_engine": False,
        "modified_bundle": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", help="Path to the extracted frozen editor bundle")
    args = parser.parse_args(argv)
    try:
        result = verify_bundle(args.bundle)
    except VerificationError as exc:
        result = {"status": "failed", "error": exc.code, "message": str(exc)}
        if exc.artifact is not None:
            result["artifact"] = exc.artifact
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": "verification_error", "message": str(exc)}, ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
