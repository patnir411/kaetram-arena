from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("TWOB_EP", "http://127.0.0.1:8101/v1")
os.environ.setdefault("FOURB_EP", "http://127.0.0.1:8102/v1")

from scripts.opd import opd_2b_data as builder  # noqa: E402
from scripts.opd import opd_data_manifest  # noqa: E402


def _source_log(root: Path, run_id: str, content: str = "session") -> Path:
    path = (
        root
        / "dataset/raw/agent_test/runs"
        / run_id
        / "session_1.log"
    )
    path.parent.mkdir(parents=True)
    path.write_text(content)
    path.with_suffix(".meta.json").write_text('{"persona":"test"}\n')
    return path


def test_source_inventory_is_complete_and_detects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    source = _source_log(tmp_path, "run_a")
    inventory = builder._snapshot_source_logs(["run_a"])
    assert inventory[0]["run_id"] == "run_a"
    assert inventory[0]["meta_path"].endswith("session_1.meta.json")
    builder._verify_source_snapshot(inventory)

    source.write_text("changed")
    with pytest.raises(RuntimeError, match="changed during"):
        builder._verify_source_snapshot(inventory)
    source.write_text("session")
    inventory = builder._snapshot_source_logs(["run_a"])
    source.with_suffix(".meta.json").write_text('{"persona":"changed"}\n')
    with pytest.raises(RuntimeError, match="metadata changed during"):
        builder._verify_source_snapshot(inventory)
    with pytest.raises(RuntimeError, match="no source logs"):
        builder._snapshot_source_logs(["missing"])
    with pytest.raises(RuntimeError, match="unique"):
        builder._snapshot_source_logs(["run_a", "run_a"])


def test_source_inventory_requires_adjacent_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    source = _source_log(tmp_path, "run_a")
    source.with_suffix(".meta.json").unlink()
    with pytest.raises(RuntimeError, match="no adjacent session metadata"):
        builder._snapshot_source_logs(["run_a"])


def test_source_inventory_materializes_bytes_and_rejects_unbound_personality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    source = _source_log(tmp_path, "run_a", "original")
    frozen = tmp_path / "frozen"
    inventory = builder._snapshot_source_logs(["run_a"], snapshot_root=frozen)
    source.write_text("changed then restored later")
    source.with_suffix(".meta.json").write_text('{"personality":"grinder"}\n')
    assert (frozen / inventory[0]["path"]).read_text() == "original"
    assert (
        frozen / inventory[0]["meta_path"]
    ).read_text() == '{"persona":"test"}\n'

    source.with_suffix(".meta.json").write_text('{"personality":"unknown"}\n')
    with pytest.raises(RuntimeError, match="unbound personality"):
        builder._snapshot_source_logs(["run_a"])


def test_declared_parse_failure_is_not_silently_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    _source_log(tmp_path, "run_a")
    inventory = builder._snapshot_source_logs(["run_a"])

    def fail(_path, *, source_repo=None, render_project_dir=None):
        raise ValueError("bad log")

    monkeypatch.setattr(builder, "reconstruct_session", fail, raising=False)
    with pytest.raises(RuntimeError, match="failed to parse declared"):
        builder.collect_action_states(inventory)


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _Client:
    def __init__(self, payload: dict):
        self.payload = payload
        self.urls: list[str] = []

    async def get(self, url: str, timeout: int) -> _Response:
        self.urls.append(url)
        assert timeout == 60
        return _Response(self.payload)


def _health() -> dict:
    return {
        "status": "ok",
        "capabilities": ["chat", "score"],
        "attestation": {
            "deployment_id": "student-deployment",
            "api_model": "2b-base",
            "checkpoint_sha256": "a" * 64,
            "tokenizer_sha256": "b" * 64,
            "render_contract_sha256": "c" * 64,
        },
    }


@pytest.mark.asyncio
async def test_endpoint_identity_is_read_from_health_and_must_match() -> None:
    client = _Client(_health())
    actual = await builder._verified_endpoint_attestation(
        client,
        "http://127.0.0.1:8101/v1",
        expected_deployment_id="student-deployment",
        expected_checkpoint_sha256="a" * 64,
    )
    assert actual == _health()["attestation"]
    assert client.urls == ["http://127.0.0.1:8101/health"]

    with pytest.raises(RuntimeError, match="does not match"):
        await builder._verified_endpoint_attestation(
            _Client(_health()),
            "http://127.0.0.1:8101/v1",
            expected_deployment_id="other-deployment",
            expected_checkpoint_sha256="a" * 64,
        )


def test_no_generic_root_attestor_is_exposed() -> None:
    assert not hasattr(opd_data_manifest, "create_opd_data_manifest")
    source = Path(builder.__file__).read_text()
    assert "open(rec_path, \"x\")" in source
    assert "There is intentionally no reusable" in source


def test_builder_manifest_publish_never_replaces_late_destination(
    tmp_path: Path,
) -> None:
    temporary = tmp_path / "temporary"
    destination = tmp_path / "destination"
    temporary.write_text("new\n")
    destination.write_text("owned\n")
    with pytest.raises(RuntimeError, match="concurrently created"):
        builder._publish_create_only(temporary, destination)
    assert destination.read_text() == "owned\n"
    assert temporary.read_text() == "new\n"


def test_material_build_inputs_snapshot_and_detect_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    monkeypatch.setattr(builder, "BUILD_SOURCE_PATHS", ("one.py", "prompt.md"))
    (tmp_path / "one.py").write_text("one\n")
    (tmp_path / "prompt.md").write_text("prompt\n")
    snapshot = builder._snapshot_build_sources()
    frozen = tmp_path / "frozen"
    builder._materialize_build_inputs(snapshot, frozen)
    assert (frozen / "prompt.md").read_text() == "prompt\n"
    builder._verify_build_source_snapshot(snapshot)
    (tmp_path / "prompt.md").write_text("changed\n")
    assert (frozen / "prompt.md").read_text() == "prompt\n"
    with pytest.raises(RuntimeError, match="material build input changed"):
        builder._verify_build_source_snapshot(snapshot)


def test_local_builder_dependencies_import_only_from_frozen_snapshot() -> None:
    repo = Path(__file__).parents[2]
    program = """
import sys
import tempfile
from pathlib import Path
from scripts.opd import opd_2b_data as builder

snapshot = builder._snapshot_build_sources()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory).resolve()
    builder._materialize_build_inputs(snapshot, root)
    builder._load_frozen_local_dependencies(root)
    names = (
        "bootstrap", "canonicalize", "eval_harness", "heldout_guard",
        "opd_probe", "opd_round1", "opd_wall_probe", "parse",
        "receipt_chain", "record_schema", "render", "tool_surface",
    )
    paths = [Path(sys.modules[name].__file__).resolve() for name in names]
    assert all(path.is_relative_to(root) for path in paths), paths
    live_repo = builder.REPO.resolve()
    live_builder = Path(builder.__file__).resolve()
    leaked = []
    for module in tuple(sys.modules.values()):
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        path = Path(module_file).resolve()
        if path.is_relative_to(live_repo):
            relative = path.relative_to(live_repo)
            if (
                path != live_builder
                and not any(part.startswith(".venv") for part in relative.parts)
            ):
                leaked.append(path)
    assert not leaked, leaked
"""
    environment = {
        **os.environ,
        "TWOB_EP": "http://127.0.0.1:8101/v1",
        "FOURB_EP": "http://127.0.0.1:8102/v1",
    }
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_frozen_dependency_loader_rejects_preloaded_live_module() -> None:
    repo = Path(__file__).parents[2]
    program = """
import tempfile
from pathlib import Path
from scripts.opd import opd_2b_data as builder
import eval_harness

snapshot = builder._snapshot_build_sources()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory).resolve()
    builder._materialize_build_inputs(snapshot, root)
    try:
        builder._load_frozen_local_dependencies(root)
    except RuntimeError as exc:
        assert "cached before the frozen import" in str(exc)
    else:
        raise AssertionError("preloaded live dependency was accepted")
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo,
        env={
            **os.environ,
            "TWOB_EP": "http://127.0.0.1:8101/v1",
            "FOURB_EP": "http://127.0.0.1:8102/v1",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_main_rejects_frozen_root_environment_bypass() -> None:
    repo = Path(__file__).parents[2]
    program = """
import asyncio
import tempfile
from pathlib import Path
from scripts.opd import opd_2b_data as builder

try:
    asyncio.run(builder.main())
except RuntimeError as exc:
    assert "must run through its frozen entrypoint" in str(exc)
else:
    raise AssertionError("live imported builder accepted a separate frozen root")
"""
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=repo,
            env={
                **os.environ,
                "TWOB_EP": "http://127.0.0.1:8101/v1",
                "FOURB_EP": "http://127.0.0.1:8102/v1",
                "KAETRAM_OPD_SOURCE_REPO": str(repo),
                "KAETRAM_OPD_FROZEN_BUILD_ROOT": directory,
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    assert result.returncode == 0, result.stderr


def test_tokenizer_directory_is_consumed_from_an_exact_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tokenizer-source"
    source.mkdir()
    (source / "tokenizer.json").write_text('{"version":1}\n')
    (source / "config.json").write_text('{"model":"test"}\n')
    expected = builder._directory_digest(source)
    frozen = builder._materialize_directory_snapshot(
        source,
        tmp_path / "tokenizer-frozen",
        expected_sha256=expected,
    )
    (source / "tokenizer.json").write_text('{"version":2}\n')
    assert (frozen / "tokenizer.json").read_text() == '{"version":1}\n'
    assert builder._directory_digest(frozen) == expected


def test_training_handoff_keeps_records_and_manifest_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    bundle = tmp_path / "dataset" / "round2"
    records = bundle / "records.jsonl"
    manifest = bundle / "records.manifest.json"
    text = builder._training_handoff_text(records, manifest)
    assert "records:  dataset/round2/records.jsonl" in text
    assert "manifest: dataset/round2/records.manifest.json" in text
    assert "--records-path <staged-bundle>/records.jsonl" in text
    assert (
        "--records-manifest-path <staged-bundle>/records.manifest.json"
        in text
    )
    assert "modal" not in text.casefold()

    external = tmp_path.parent / "external-opd-handoff"
    external_text = builder._training_handoff_text(
        external / "records.jsonl",
        external / "records.manifest.json",
    )
    assert f"records:  {external / 'records.jsonl'}" in external_text
    assert f"manifest: {external / 'records.manifest.json'}" in external_text


@pytest.mark.parametrize(
    "relative",
    ["finetune/serve_modal_4b.py", "finetune/serve_modal_2b_opd.py"],
)
def test_scoring_servers_expose_endpoint_identity(relative: str) -> None:
    source = (Path(__file__).parents[2] / relative).read_text()
    assert ".add_local_python_source(\"endpoint_identity\")" in source
    assert "endpoint_attestation(" in source
    assert '"attestation": attestation' in source
