"""Build and run the isolated numeric BufferRD fixture with an explicit custom SDK.

No engine build, export, screenshots, focus changes, or global NuGet changes.
Run this only after the coordinator has produced the matching engine/SDK bundle.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from xml.sax.saxutils import quoteattr

PROJECT = Path(__file__).resolve().parent / "project"
INPUTS = ("BufferRdLab.csproj", "project.godot", "Main.tscn", "BufferRdContract.cs",
          "ContractErrorLogger.cs", "Producer.glslinc", "Consumer.gdshader")
CASES = {"initial-compute": 2, "same-rid-producer-update": 2,
         "oracle-negative-control": 2, "oob-length-and-uint-max": 2,
         "one-consumer-detached": 1, "replacement-and-original-independent": 2,
         "replacement-shared-consumers": 2, "binding-guards-preserve-a": 2,
         "missing-buffer-zero-record": 1}
INVALID_BIND = "Read-only buffer shader parameters require a live, attached BufferRD resource."
THREAD_BIND = "BufferRD shader parameter assignments require the render thread."
GUARDS = {"bare-rid": INVALID_BIND, "empty-wrapper": INVALID_BIND,
          "freed-before-first-bind": INVALID_BIND, "worker-thread-binding": THREAD_BIND}
DIAGNOSTIC = re.compile(r"\b(?:ERROR:|SCRIPT ERROR:|FATAL(?: ERROR)?[: ]|Unhandled exception)|"
                        r"WARNING:.*(?:leak|RID.*(?:allocated|still|remain)|orphan)", re.I)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def source_hashes() -> dict[str, str]:
    return {name: sha(PROJECT / name) for name in INPUTS}


def validate(record: dict, engine_hash: str, sdk_version: str) -> dict:
    expected = {"contract": "buffer-rd-numeric-contract-v1", "passed": True,
                "errors": 0, "cycles": 32, "warmup_cycles": 8, "completed_cycles": 40,
                "engine_hash": engine_hash, "sdk_version": sdk_version,
                "driver": "vulkan", "renderer": "forward_plus",
                "attach_ok_calls": 121, "wrapper_creations": 82, "wrapper_disposals": 82,
                "compute_dispatches": 120, "numeric_viewport_audits": 562,
                "numeric_pixel_samples": 1686, "viewport_image_readbacks": 562,
                "total_native_error_callbacks": 4,
                "final_owned_gpu_buffer_count": 0, "final_owned_rd_resource_count": 0,
                "final_wrapper_count": 0, "final_material_count": 0,
                "managed_collection_boundaries": 40,
                "final_owned_requested_gpu_buffer_bytes": 0, "leak_growth_tolerance_bytes": 0,
                "latter_half_resource_growth_tolerance": 0,
                "max_live_positive_storage_bytes": 128,
                "image_files_written": 0, "performance_window_count": 0}
    errors = [f"{key}: expected {value!r}, got {record.get(key)!r}"
              for key, value in expected.items() if record.get(key) != value]
    if record.get("failure"):
        errors.append(record["failure"])
    callbacks = record.get("expected_native_error_callbacks", [])
    if len(callbacks) != 4 or {item.get("test") for item in callbacks} != set(GUARDS):
        errors.append("Incomplete exact expected native guard callback ledger.")
    for item in callbacks:
        file = item.get("file", "").replace("\\", "/")
        if (item.get("rationale") != GUARDS.get(item.get("test")) or item.get("error_type") != 0
                or item.get("function") not in {"set_shader_parameter", "ShaderMaterial::set_shader_parameter"}
                or not (file == "scene/resources/material.cpp" or file.endswith("/scene/resources/material.cpp"))
                or not isinstance(item.get("line"), int) or item["line"] <= 0 or not item.get("code")):
            errors.append("Expected guard callback has an incompatible function/file/rationale.")
    binding_guards = record.get("material_binding_guard_audits", [])
    if len(binding_guards) != 4 or {item.get("test") for item in binding_guards} != set(GUARDS):
        errors.append("Incomplete material binding preservation ledger.")
    for item in binding_guards:
        if item.get("cached_previous_wrapper_preserved") is not True or item.get("unexpected_error_delta") != 0:
            errors.append("A material guard changed its previous cached wrapper or emitted an unexpected error.")
        if item.get("test") == "freed-before-first-bind" and (item.get("backing_freed_before_first_material_bind") is not True
                or not isinstance(item.get("retired_storage_rid"), int) or item["retired_storage_rid"] <= 0
                or item["retired_storage_rid"] == item.get("previous_storage_rid")):
            errors.append("The stale-wrapper probe lacks a distinct, freed backing RID.")
    negatives = record.get("negative_audits", [])
    names = {"invalid-rid", "freed-rid", "uniform-buffer", "undersized-storage",
             "non-record-aligned-storage", "different-valid-storage",
             "local-device-storage", "off-render-thread"}
    if len(negatives) != 8 or {item.get("name") for item in negatives} != names:
        errors.append("The eight distinct rejected attachments are incomplete.")
    for item in negatives:
        expected_error = {"different-valid-storage": "AlreadyInUse", "off-render-thread": "Unavailable"}.get(item.get("name"), "InvalidParameter")
        if (item.get("expected_error") != expected_error or item.get("actual_error") != expected_error
                or item.get("preserved") is not True or item.get("error_delta") != 0):
            errors.append("Rejected attachment did not preserve the existing binding quietly.")
    identities = record.get("borrowed_identity_audits", [])
    if len(identities) != 120:
        errors.append("Incomplete borrowed RID identity ledger.")
    original = next((item for item in identities if item.get("cycle") == 8 and item.get("test") == "initial"), {})
    for item in binding_guards:
        if (item.get("previous_wrapper_resource_id") != original.get("wrapper_resource_id")
                or item.get("previous_storage_rid") != original.get("caller_storage_rid")):
            errors.append("Material guard preservation does not refer to first-settled-cycle wrapper A.")
    for cycle in range(40):
        rows = [item for item in identities if item.get("cycle") == cycle]
        by_name = {item.get("test"): item for item in rows}
        if len(rows) != 3 or set(by_name) != {"initial", "producer-update", "replacement"}:
            errors.append("Incomplete per-cycle borrowed RID identities.")
            continue
        for item in rows:
            rid = item.get("caller_storage_rid")
            if not isinstance(rid, int) or rid <= 0 or item.get("wrapper_get_rid") != rid or item.get("wrapper_buffer_rd_rid") != rid:
                errors.append("Wrapper getters differ from actual caller storage.")
        if (by_name["initial"]["caller_storage_rid"] != by_name["producer-update"]["caller_storage_rid"]
                or by_name["initial"]["wrapper_resource_id"] != by_name["producer-update"]["wrapper_resource_id"]
                or by_name["initial"]["caller_storage_rid"] == by_name["replacement"]["caller_storage_rid"]
                or by_name["initial"]["wrapper_resource_id"] == by_name["replacement"]["wrapper_resource_id"]):
            errors.append("Same-RID updates or new-wrapper replacement identity is invalid.")
    cycles = record.get("cycle_audits", [])
    if len(cycles) != 40 or [item.get("cycle") for item in cycles] != list(range(40)):
        errors.append("Incomplete ordered lifecycle ledger.")
    for item in cycles:
        if (item.get("raster_audits") != (16 if item.get("cycle") == 8 else 14) or item.get("final_owned_buffers") != 0
                or item.get("final_wrappers") != 0 or item.get("final_materials") != 0
                or item.get("warmup") != (item.get("cycle", 40) < 8)):
            errors.append("A lifecycle row is incomplete or retained resources.")
    audits = record.get("representative_numeric_audits", [])
    if Counter(item.get("test") for item in audits) != Counter(CASES):
        errors.append("Representative numeric shader cases are incomplete.")
    for item in audits:
        expected_rgb = [0, 0, 255] if item.get("test") == "oracle-negative-control" else [255, 255, 255]
        observed = item.get("observed_rgb", [])
        if (item.get("expected_rgb") != expected_rgb or len(observed) != 9
                or any(not isinstance(value, int) or abs(value - expected_rgb[index % 3]) > 1
                       for index, value in enumerate(observed))):
            errors.append("Numerical shader output does not match its declared oracle.")
    for field in ("settled_rd_logical_buffer_bytes", "settled_native_object_counts"):
        values = record.get(field, [])
        if (len(values) != 32 or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values)
                or len(set(values[16:])) != 1):
            errors.append(field + " lacks an exact latter-half plateau.")
    if errors:
        raise ValueError("\n".join(errors))
    return {"passed": True, "warmup_cycles": 8, "settled_cycles": 32,
            "numeric_viewport_audits": 562, "negative_attach_cases": 8, "material_binding_guard_cases": 4,
            "final_owned_resources": 0,
            "memory_scope": "Logical RD bytes and native ObjectDB object plateau; not physical VRAM residency.",
            "performance_qualified": False}


def inspect_shutdown(console: str, callbacks: list[dict]) -> dict:
    """Permit exactly one scoped native error/location pair for each raw callback.

    Identical diagnostics outside the explicit guard markers remain failures,
    including errors after contract.json or during process shutdown.
    """
    failures: list[dict] = []
    expected_lines: list[dict] = []
    by_name = {item.get("test"): item for item in callbacks}
    if len(callbacks) != 4 or set(by_name) != set(GUARDS):
        failures.append({"line": 0, "text": "Expected callback ledger is missing or duplicated."})
    active = None
    seen: set[str] = set()
    error_count = location_count = 0
    for number, line in enumerate(console.splitlines(), 1):
        if line.startswith("BUFFER_RD_EXPECTED_GUARD_BEGIN="):
            name = line.split("=", 1)[1]
            if active is not None or name not in by_name or name in seen:
                failures.append({"line": number, "text": "Invalid expected-error begin marker: " + line})
            active = name
            error_count = location_count = 0
            continue
        if line.startswith("BUFFER_RD_EXPECTED_GUARD_END="):
            name = line.split("=", 1)[1]
            if name != active or error_count != 1 or location_count != 1:
                failures.append({"line": number, "text": "Guard requires exactly one matching error and native source location: " + name})
            seen.add(name)
            active = None
            continue
        callback = by_name.get(active, {})
        if callback:
            location = f"at: {callback.get('function')} ({callback.get('file')}:{callback.get('line')})"
            if line.strip() == location:
                location_count += 1
        if DIAGNOSTIC.search(line):
            if (callback and callback.get("rationale") == GUARDS.get(active)
                    and line == "ERROR: " + callback["rationale"] and error_count == 0):
                error_count += 1
                expected_lines.append({"line": number, "test": active, "text": line})
            else:
                failures.append({"line": number, "text": line})
    if active is not None or seen != set(GUARDS) or len(expected_lines) != 4:
        failures.append({"line": 0, "text": "Incomplete bounded expected native error scopes."})
    return {"passed": not failures, "expected_native_error_count": len(expected_lines),
            "expected_diagnostics": expected_lines, "diagnostics": failures}


def execute(command: list[str], cwd: Path, env: dict[str, str], output: Path, label: str, timeout: int) -> int:
    started = time.time()
    receipt = {"command": command, "cwd": str(cwd), "started_unix_s": started}
    code = -1
    try:
        with (output / f"{label}.log").open("wb") as log:
            completed = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
            code = completed.returncode
        return code
    except subprocess.TimeoutExpired:
        receipt["timeout"] = True
        raise
    finally:
        receipt.update(exit_code=code, elapsed_s=time.time() - started)
        dump(output / f"{label}-exit.json", receipt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path, help="Matching .NET editor executable; not a console wrapper.")
    parser.add_argument("--sdk-feed", required=True, type=Path, help="Local feed containing this editor's exact generated packages.")
    parser.add_argument("--output", required=True, type=Path, help="Fresh evidence/work directory outside the fixture project.")
    parser.add_argument("--engine-hash", required=True, help="Full coordinator-committed native source SHA.")
    parser.add_argument("--sdk-version", default=os.environ.get("ArenaGodotSdkVersion"))
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    engine, feed, output = args.engine.resolve(), args.sdk_feed.resolve(), args.output.resolve()
    if not engine.is_file() or not feed.is_dir() or not args.sdk_version:
        parser.error("Existing engine/feed and an explicit SDK version or ArenaGodotSdkVersion are required.")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.engine_hash) or args.timeout < 1:
        parser.error("A full 40-character engine SHA and positive timeout are required.")
    if output == PROJECT or PROJECT in output.parents:
        parser.error("Keep evidence/work output outside the source fixture project.")
    before = source_hashes()
    engine_before = sha(engine)
    api_path = engine.parent / "GodotSharp/Api/Debug/GodotSharp.dll"
    if not api_path.is_file():
        parser.error("The matching editor's complete sibling GodotSharp tree is required.")
    api_before = sha(api_path)
    output.mkdir(parents=True, exist_ok=False)
    work = output / "work"
    work.mkdir()
    project = work / "project"
    project.mkdir()
    for name in INPUTS:
        shutil.copy2(PROJECT / name, project / name)
    env = os.environ.copy()
    for key, folder in {"TEMP": "tmp", "TMP": "tmp", "APPDATA": "appdata", "LOCALAPPDATA": "localappdata",
                        "DOTNET_CLI_HOME": "dotnet-home", "NUGET_PACKAGES": "nuget-packages",
                        "NUGET_HTTP_CACHE_PATH": "nuget-http", "NUGET_PLUGINS_CACHE_PATH": "nuget-plugins",
                        "NUGET_SCRATCH": "nuget-scratch"}.items():
        path = work / folder
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    # SDK resolution happens before restore's --configfile is applied. Keep the
    # isolated config beside the staged project so ancestor/user feeds cannot
    # redirect the generated SDK to a different engine build.
    config = project / "NuGet.Config"
    config.write_bytes(('<?xml version="1.0" encoding="utf-8"?>\n<configuration><packageSources><clear />'
        '<add key="BufferRdLocal" value=' + quoteattr(feed.as_posix()) + ' />'
        '<add key="nuget.org" value="https://api.nuget.org/v3/index.json" /></packageSources>'
        '<packageSourceMapping><clear /><packageSource key="BufferRdLocal"><package pattern="Godot.*" />'
        '<package pattern="GodotSharp" /><package pattern="GodotSharpEditor" /></packageSource>'
        '<packageSource key="nuget.org"><package pattern="*" /></packageSource></packageSourceMapping>'
        '</configuration>\n').encode())
    user_config = work / "appdata/NuGet/NuGet.Config"
    user_config.parent.mkdir(parents=True)
    user_config.write_bytes(config.read_bytes())
    env.update(ArenaGodotSdkVersion=args.sdk_version, RestoreConfigFile=str(config),
               DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
               DOTNET_GENERATE_ASPNET_CERTIFICATE="false",
               DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE="1", DOTNET_CLI_USE_MSBUILD_SERVER="0",
               MSBUILDDISABLENODEREUSE="1", UseSharedCompilation="false")
    obj = (work / "obj").as_posix() + "/"
    properties = [f"-p:ArenaGodotSdkVersion={args.sdk_version}", f"-p:BaseIntermediateOutputPath={obj}",
                  f"-p:MSBuildProjectExtensionsPath={obj}", f"-p:IntermediateOutputPath={obj}Debug/",
                  "-p:Optimize=true", "-p:UseSharedCompilation=false"]
    csproj = project / "BufferRdLab.csproj"
    run = {"engine": str(engine), "engine_sha256": engine_before, "engine_source_hash": args.engine_hash,
           "managed_api_sha256": api_before, "sdk_feed": str(feed), "sdk_version": args.sdk_version,
           "project": str(project), "source_project": str(PROJECT), "source_sha256": before,
           "owned_output": str(output), "generated_project_output": str(project / ".godot"),
           "scope": "Procedural numeric GPU correctness and bounded lifetime; no performance gate."}
    dump(output / "run.json", run)
    try:
        restore = [args.dotnet, "restore", str(csproj), "--configfile", str(config), "--disable-parallel", *properties]
        build = [args.dotnet, "build", str(csproj), "--configuration", "Debug", "--no-restore", "--no-incremental",
                 "--disable-build-servers", "-maxcpucount:2", *properties]
        for label, command in (("restore", restore), ("build", build)):
            if execute(command, project, env, output, label, args.timeout):
                raise ValueError(label + " failed; inspect only its failed log.")
        assets = json.loads((Path(obj) / "project.assets.json").read_bytes())
        for package in ("GodotSharp", "GodotSharpEditor", "Godot.SourceGenerators"):
            restored = [name for name in assets["libraries"] if name.startswith(package + "/")]
            if restored != [package + "/" + args.sdk_version]:
                raise ValueError("Restored incompatible managed API: " + repr(restored))
        fixture_assembly = project / ".godot/mono/temp/bin/Debug/BufferRdLab.dll"
        fixture_before = sha(fixture_assembly)
        if source_hashes() != before or {name: sha(project / name) for name in INPUTS} != before:
            raise ValueError("Authored source changed while building the fixture.")
        run["managed_fixture_assembly_sha256"] = fixture_before
        dump(output / "run.json", run)
        record = output / "contract.json"
        command = [str(engine), "--path", str(project), "--rendering-method", "forward_plus", "--rendering-driver", "vulkan",
                   "--", "--record=" + str(record), "--expected-engine-hash=" + args.engine_hash,
                   "--expected-sdk-version=" + args.sdk_version]
        code = execute(command, project, env, output, "runtime", args.timeout)
        raw = json.loads(record.read_bytes()) if record.is_file() else {}
        shutdown = inspect_shutdown((output / "runtime.log").read_text(encoding="utf-8", errors="replace"),
                                    raw.get("expected_native_error_callbacks", []))
        shutdown.update(passed=shutdown["passed"] and code == 0, exit_code=code,
                        console_sha256=sha(output / "runtime.log"))
        dump(output / "shutdown.json", shutdown)
        if code or not shutdown["passed"]:
            raise ValueError("Runtime/shutdown gate failed; exact diagnostics are in shutdown.json.")
        summary = validate(raw, args.engine_hash, args.sdk_version)
        if raw["managed_api_assembly_sha256"] != api_before:
            raise ValueError("Runtime loaded a GodotSharp assembly from another bundle.")
        if raw["managed_fixture_assembly_sha256"] != fixture_before or sha(fixture_assembly) != fixture_before:
            raise ValueError("Runtime fixture assembly differs from the just-built assembly.")
        if (before != source_hashes() or {name: sha(project / name) for name in INPUTS} != before
                or engine_before != sha(engine) or api_before != sha(api_path)):
            raise ValueError("Authored source or engine bundle changed during the contract.")
        summary.update(contract_sha256=sha(record), source_sha256=before,
                       shutdown_receipt_sha256=sha(output / "shutdown.json"))
        dump(output / "acceptance.json", summary)
        print("BufferRD numeric contract PASS: " + str(output / "acceptance.json"))
        return 0
    except Exception as error:
        dump(output / "acceptance.json", {"passed": False, "failure": str(error), "performance_qualified": False})
        print("BufferRD contract failed: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
