from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from run_manifest import sha256_json
from scripts.build_hf_snapshot_lock import SCHEMA_VERSION
from scripts.fetch_hf_snapshot import locked_snapshot_tree_sha256
from scripts.local_mlx_endpoint import (
    CANONICAL_TOKENIZER_SNAPSHOT,
    LocalEndpointError,
    PINNED_MLX_LM_VERSION,
    SEEDED_SAMPLING_CONTRACT_SCHEMA,
    SEEDED_SERVER_SCRIPT,
    build_backend_command,
    build_identity,
    build_render_contract,
    build_runtime_view,
    normalize_mlx_tool_arguments,
    require_loopback,
    rewrite_chat_request,
    rewrite_chat_response,
    verify_seeded_sampler_runtime,
)


def _lock() -> dict:
    snapshot = {
        "repo_type": "model",
        "repo_id": "owner/model",
        "revision": "a" * 40,
        "file_count": 3,
        "size_bytes": 9,
        "files": [
            {
                "path": "model.safetensors",
                "size_bytes": 3,
                "sha256": hashlib.sha256(b"abc").hexdigest(),
            },
            {
                "path": "tokenizer.json",
                "size_bytes": 4,
                "sha256": hashlib.sha256(b"tokn").hexdigest(),
            },
            {
                "path": "tokenizer_config.json",
                "size_bytes": 2,
                "sha256": hashlib.sha256(b"{}").hexdigest(),
            },
        ],
    }
    lock = {
        "schema_version": SCHEMA_VERSION,
        "source": "https://huggingface.co",
        "snapshots": {"base_2b": snapshot},
    }
    lock["lock_sha256"] = sha256_json(lock)
    return lock


def _render_contract(tmp_path: Path) -> dict:
    canonical_dir = tmp_path / CANONICAL_TOKENIZER_SNAPSHOT
    canonical_dir.mkdir(exist_ok=True)
    (canonical_dir / "tokenizer_config.json").write_text("{}")
    return build_render_contract(
        _lock(),
        "patched-template",
        canonical_dir,
        {"rendered_text_sha256": "f" * 64},
        {
            "schema_version": SEEDED_SAMPLING_CONTRACT_SCHEMA,
            "mlx_lm_version": PINNED_MLX_LM_VERSION,
            "distinct_seed_outputs": 4,
            "execution_thread": "background",
        },
    )


def test_identity_is_derived_only_from_locked_artifacts(tmp_path: Path) -> None:
    render_contract = _render_contract(tmp_path)
    identity = build_identity(
        _lock(), "base_2b", "2b-base", render_contract, "e" * 64
    )
    assert identity.deployment_id == (
        f"local-mlx-lm-{PINNED_MLX_LM_VERSION}-base_2b-"
        + "a" * 12
        + "-"
        + sha256_json(render_contract)[:12]
    )
    assert identity.checkpoint_sha256 == hashlib.sha256(b"abc").hexdigest()
    assert identity.snapshot_tree_sha256 == locked_snapshot_tree_sha256(
        _lock()["snapshots"]["base_2b"]
    )
    assert identity.snapshot_lock_sha256 == _lock()["lock_sha256"]
    assert identity.tokenizer_sha256 == hashlib.sha256(b"tokn").hexdigest()
    assert identity.render_contract_sha256 == sha256_json(render_contract)
    assert identity.chat_template_sha256 == hashlib.sha256(
        b"patched-template"
    ).hexdigest()
    assert identity.tokenizer_source_revision == "a" * 40
    assert identity.fix_mistral_regex is False
    assert identity.sampling_contract_sha256 == sha256_json(
        render_contract["seeded_sampling"]
    )
    assert render_contract["seeded_sampling"]["server_script_sha256"] == (
        hashlib.sha256(SEEDED_SERVER_SCRIPT.read_bytes()).hexdigest()
    )
    assert identity.health_payload()["attestation"]["api_model"] == "2b-base"
    assert identity.health_payload()["attestation"]["snapshot_tree_sha256"] == (
        identity.snapshot_tree_sha256
    )
    assert (
        identity.health_payload()["attestation"][
            "runtime_environment_receipt_sha256"
        ]
        == "e" * 64
    )


def test_identity_rejects_scientific_alias_drift(tmp_path: Path) -> None:
    with pytest.raises(LocalEndpointError, match="reviewed API model"):
        build_identity(
            _lock(),
            "base_2b",
            "wrong-name",
            _render_contract(tmp_path),
            "e" * 64,
        )


def test_identity_rejects_missing_runtime_environment_receipt(
    tmp_path: Path,
) -> None:
    with pytest.raises(LocalEndpointError, match="runtime environment receipt"):
        build_identity(
            _lock(),
            "base_2b",
            "2b-base",
            _render_contract(tmp_path),
            "",
        )


def test_runtime_view_uses_canonical_tokenizer_without_mutating_sources(
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "arm"
    canonical_dir = tmp_path / CANONICAL_TOKENIZER_SNAPSHOT
    runtime_dir = tmp_path / "runtime"
    model_dir.mkdir()
    canonical_dir.mkdir()
    (model_dir / "config.json").write_text('{"arm":"r2"}')
    (model_dir / "model.safetensors").write_bytes(b"weights")
    (model_dir / "tokenizer.json").write_text('{"arm":"r2"}')
    original_config = {
        "chat_template": "unpatched",
        "tokenizer_class": "Qwen2Tokenizer",
    }
    (canonical_dir / "tokenizer.json").write_text('{"canonical":true}')
    (canonical_dir / "tokenizer_config.json").write_text(
        json.dumps(original_config)
    )
    (canonical_dir / "chat_template.jinja").write_text("unpatched")
    (canonical_dir / "merges.txt").write_text("merges")
    (model_dir / "unlocked.bin").write_bytes(b"must-not-enter-runtime")

    build_runtime_view(
        model_dir,
        canonical_dir,
        runtime_dir,
        "patched-template",
        model_snapshot={"files": [
            {"path": "config.json"},
            {"path": "model.safetensors"},
            {"path": "tokenizer.json"},
        ]},
        canonical_tokenizer_snapshot={"files": [
            {"path": "tokenizer.json"},
            {"path": "tokenizer_config.json"},
            {"path": "chat_template.jinja"},
            {"path": "merges.txt"},
        ]},
    )

    assert (runtime_dir / "config.json").read_text() == '{"arm":"r2"}'
    assert (runtime_dir / "model.safetensors").read_bytes() == b"weights"
    assert (runtime_dir / "tokenizer.json").read_text() == '{"canonical":true}'
    assert (runtime_dir / "merges.txt").read_text() == "merges"
    runtime_config = json.loads((runtime_dir / "tokenizer_config.json").read_text())
    assert runtime_config["chat_template"] == "patched-template"
    assert runtime_config["fix_mistral_regex"] is False
    assert (runtime_dir / "chat_template.jinja").read_text() == "patched-template"
    assert not (runtime_dir / "unlocked.bin").exists()
    assert json.loads((canonical_dir / "tokenizer_config.json").read_text()) == (
        original_config
    )


def test_request_rewrite_preserves_payload_and_hides_backend_path() -> None:
    source = {
        "model": "2b-base",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "observe"}}],
        "temperature": 0.6,
    }
    rewritten = json.loads(
        rewrite_chat_request(json.dumps(source).encode(), "2b-base")
    )
    assert rewritten == {**source, "model": "default_model"}

    with pytest.raises(LocalEndpointError, match="attested API model"):
        rewrite_chat_request(b'{"model":"2b-opd-r2"}', "2b-base")


def test_request_rewrite_normalizes_multi_turn_tool_history_for_mlx() -> None:
    observe_args = {}
    warp_args = {"location": "müdwich", "options": {"safe": True}}
    spaced_args = '{  "x": 188, "y": 157 }'
    source = {
        "model": "2b-base",
        "messages": [
            {"role": "system", "content": "play"},
            {
                "role": "assistant",
                "tool_calls": [{
                    "type": "function",
                    "function": {"name": "observe", "arguments": observe_args},
                }],
            },
            {"role": "tool", "content": '{"pos":{"x":328,"y":892}}'},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "warp", "arguments": warp_args},
                    },
                    {
                        "type": "function",
                        "function": {"name": "navigate", "arguments": spaced_args},
                    },
                ],
            },
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "warp",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        "seed": 17,
    }

    rewritten = json.loads(
        rewrite_chat_request(json.dumps(source).encode(), "2b-base")
    )
    calls = [
        call
        for message in rewritten["messages"]
        for call in message.get("tool_calls", [])
    ]
    assert json.loads(calls[0]["function"]["arguments"]) == observe_args
    assert json.loads(calls[1]["function"]["arguments"]) == warp_args
    assert calls[2]["function"]["arguments"] == spaced_args
    assert rewritten["tools"] == source["tools"]
    assert rewritten["messages"][2] == source["messages"][2]
    assert rewritten["seed"] == 17


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [],
        1,
        True,
        "",
        "{broken",
        "[]",
        "null",
        "true",
    ],
)
def test_mlx_history_rejects_non_object_tool_arguments(arguments: object) -> None:
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "type": "function",
            "function": {"name": "observe", "arguments": arguments},
        }],
    }]
    with pytest.raises(
        LocalEndpointError,
        match=r"messages\[0\]\.tool_calls\[0\]\.function\.arguments",
    ):
        normalize_mlx_tool_arguments(messages)


def test_mlx_history_rejects_non_json_mapping_values() -> None:
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "type": "function",
            "function": {"name": "navigate", "arguments": {"x": float("nan")}},
        }],
    }]
    with pytest.raises(LocalEndpointError, match="JSON object"):
        normalize_mlx_tool_arguments(messages)


def test_response_rewrite_restores_alias_and_thinking_shape() -> None:
    source = {
        "model": "default_model",
        "choices": [{
            "message": {
                "reasoning": "I should observe.",
                "content": "\n",
                "tool_calls": [{"function": {"name": "observe", "arguments": "{}"}}],
            }
        }],
    }
    rewritten = json.loads(
        rewrite_chat_response(json.dumps(source).encode(), "2b-base")
    )
    assert rewritten["model"] == "2b-base"
    assert rewritten["choices"][0]["message"]["content"] == (
        "<think>I should observe.</think>\n"
    )
    assert rewritten["choices"][0]["message"]["tool_calls"] == (
        source["choices"][0]["message"]["tool_calls"]
    )


def test_loopback_is_mandatory_for_both_listeners() -> None:
    for host in ("127.0.0.1", "::1", "localhost"):
        require_loopback(host)
    with pytest.raises(LocalEndpointError, match="non-loopback"):
        require_loopback("0.0.0.0")


def test_backend_command_is_pinned_to_local_snapshot(tmp_path: Path) -> None:
    command = build_backend_command(
        "/venv/bin/python",
        tmp_path / "base_2b",
        "127.0.0.1",
        8082,
        "patched-template",
    )
    assert command[0] == "/venv/bin/python"
    assert command[1:4] == ["-I", "-S", "-B"]
    assert command[command.index("--script") + 1] == str(SEEDED_SERVER_SCRIPT)
    assert command[command.index("--") + 1] == "--model"
    assert command[command.index("--model") + 1] == str(tmp_path / "base_2b")
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8082"
    assert command[command.index("--chat-template") + 1] == "patched-template"
    assert '{"enable_thinking":true}' in command


def test_seeded_sampler_runtime_probe_requires_distinct_background_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "schema_version": SEEDED_SAMPLING_CONTRACT_SCHEMA,
        "mlx_lm_version": PINNED_MLX_LM_VERSION,
        "distinct_seed_outputs": 3,
        "execution_thread": "background",
    }
    monkeypatch.setattr(
        "scripts.local_mlx_endpoint.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    assert verify_seeded_sampler_runtime("/venv/bin/python") == payload

    payload["distinct_seed_outputs"] = 1
    with pytest.raises(LocalEndpointError, match="identity mismatch"):
        verify_seeded_sampler_runtime("/venv/bin/python")


def test_seeded_sampler_runtime_probe_fails_closed_on_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.local_mlx_endpoint.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="broken",
        ),
    )

    with pytest.raises(LocalEndpointError, match="reviewed contract"):
        verify_seeded_sampler_runtime("/venv/bin/python")
