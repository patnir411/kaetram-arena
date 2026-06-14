#!/usr/bin/env python3
"""
cli_adapter.py — Abstraction layer for different AI CLI tools (Claude Code, Codex).

Provides adapters that encapsulate CLI-specific differences: command construction,
sandbox configuration, environment variables, and log parsing.
"""

import json
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
MCP_JSON = PROJECT_DIR / ".mcp.template.json"

# Disallowed tools for the game agent (prevent filesystem exploration;
# agent should only use mcp__kaetram__* tools).
CLAUDE_DISALLOWED_TOOLS = "Bash Glob Grep Agent Edit WebFetch WebSearch Write Skill Read ToolSearch CronList CronCreate CronDelete NotebookEdit TodoWrite TaskCreate TaskUpdate TaskGet TaskList TaskOutput TaskStop EnterPlanMode ExitPlanMode EnterWorktree ExitWorktree RemoteTrigger"

# Venv python path for MCP server subprocess
VENV_PYTHON = str(PROJECT_DIR / ".venv" / "bin" / "python3")


def _resolve_mcp_template(sandbox_dir: Path, port: str = "", username: str = "ClaudeBot") -> str:
    """Resolve .mcp.json template variables for a given sandbox directory.

    Only includes the 'kaetram' MCP server — other servers (linear, etc.)
    are stripped to avoid startup delays and resource contention.
    """
    import json as _json
    raw = _json.loads(MCP_JSON.read_text())
    # Keep only kaetram server for agent sandboxes
    kaetram_cfg = raw.get("mcpServers", {}).get("kaetram")
    if not kaetram_cfg:
        raise RuntimeError("No 'kaetram' server in .mcp.json template")
    text = _json.dumps({"mcpServers": {"kaetram": kaetram_cfg}}, indent=2)
    state_dir = str(sandbox_dir / "state")
    return (text
            .replace("__VENV_PYTHON__", VENV_PYTHON)
            .replace("__PROJECT_DIR__", str(PROJECT_DIR))
            .replace("__STATE_DIR__", state_dir)
            .replace("__SERVER_PORT__", str(port))
            .replace("__USERNAME__", username)
            )


OPENCODE_MODEL_ALIASES: dict[str, str] = {
    "grok-4-1-fast":     "xai/grok-4-1-fast-reasoning",
    "qwen3.5-35a3b":     "nvidia/qwen/qwen3.5-35b-a3b",
    "qwen3.5-397a17b":   "nvidia/qwen/qwen3.5-397b-a17b",
    "qwen3-80a3b":       "nvidia/qwen/qwen3-next-80b-a3b-thinking",
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "deepseek-v4-pro":   "deepseek/deepseek-v4-pro",
}


def resolve_opencode_model(name: str | None) -> str | None:
    """Map shorthand model name to opencode-config model ID.

    Pass-through when already qualified (contains '/'), so power users can
    specify exact provider/model strings directly. Returns None for empty
    input — callers treat that as 'use template default'.
    """
    if not name:
        return None
    return OPENCODE_MODEL_ALIASES.get(name, name)


def opencode_bot_prefix(model_id: str | None) -> str:
    """Bot-username prefix for an opencode run, by model family.

    The prefix is what shows up as the in-game username + Mongo player row,
    so dashboard / DB / log analysis can distinguish runs across model
    families. Splits opencode by family rather than lumping everything into
    a single 'OpenCodeBot' bucket.

    - any "qwen"     → BigQwenBot   (separate from the in-house Qwen harness,
                                    which uses personality-based names)
    - any "grok"     → GrokBot
    - any "deepseek" → DeepSeekBot
    - fallback       → OpenCodeBot
    """
    if not model_id:
        return "OpenCodeBot"
    m = model_id.lower()
    if "qwen" in m:
        return "BigQwenBot"
    if "grok" in m:
        return "GrokBot"
    if "deepseek" in m:
        return "DeepSeekBot"
    return "OpenCodeBot"


class CLIAdapter(ABC):
    """Base class for AI CLI tool adapters."""

    def __init__(self, model: str):
        self.model = model

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this adapter (e.g. 'claude', 'codex')."""

    @abstractmethod
    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        """Write CLI-specific config files to the agent sandbox.

        Called each session so that dynamic content (like AGENTS.md for Codex)
        gets refreshed. Idempotent — safe to call multiple times.
        """

    @abstractmethod
    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
        personality: str | None = None,
        session_n: int = 1,
    ) -> list[str]:
        """Build the CLI command to launch an agent session.

        `personality` and `session_n` are passed for adapters that need to
        rebuild the orchestrate bootstrap on the subprocess side (e.g.
        QwenAdapter). Other adapters ignore them — Claude/Codex/Gemini/
        OpenCode receive the already-built `user_prompt` directly.
        """

    @abstractmethod
    def get_env(self) -> dict[str, str]:
        """Extra environment variables for the subprocess."""

class ClaudeAdapter(CLIAdapter):
    """Adapter for Claude Code CLI (claude -p)."""

    def __init__(self, model: str = "sonnet"):
        super().__init__(model)
        self._mcp_config_path: str | None = None

    @property
    def name(self) -> str:
        return "claude"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        mcp_text = _resolve_mcp_template(sandbox_dir, port=port, username=username)
        mcp_path = sandbox_dir / ".mcp.json"
        mcp_path.write_text(mcp_text)
        self._mcp_config_path = str(mcp_path)

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
        personality: str | None = None,
        session_n: int = 1,
    ) -> list[str]:
        cmd = [
            "claude",
            "-p",
            user_prompt,
            "--model",
            self.model,
            "--max-turns",
            str(max_turns),
            "--append-system-prompt",
            system_prompt,
            "--dangerously-skip-permissions",
            "--disallowedTools",
            CLAUDE_DISALLOWED_TOOLS,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        # Use sandbox MCP config (not project-level .mcp.json)
        if self._mcp_config_path:
            cmd.extend(["--mcp-config", self._mcp_config_path, "--strict-mcp-config"])
        if max_budget_usd is not None and auth_mode == "api_key":
            cmd.extend(["--max-budget-usd", str(max_budget_usd)])
        return cmd

    def get_env(self) -> dict[str, str]:
        return {
            "CLAUDECODE": "",
            "MCP_TIMEOUT": "60000",  # 60s timeout for MCP server startup (3 concurrent browser launches)
        }


class CodexAdapter(CLIAdapter):
    """Adapter for OpenAI Codex CLI (codex exec)."""

    def __init__(self, model: str = "gpt-5.4"):
        super().__init__(model)
        self._codex_home: Path | None = None

    @property
    def name(self) -> str:
        return "codex"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        # Write system prompt to a file that we'll reference via -c model_instructions_file.
        # AGENTS.md alone is too weak — Codex treats it as "guidance" not strict instructions.
        # model_instructions_file is injected as developer instructions and is respected.
        if system_prompt:
            (sandbox_dir / "AGENTS.md").write_text(system_prompt)
            (sandbox_dir / "system_prompt.md").write_text(system_prompt)

        # Codex requires a git repo — init one if missing
        git_dir = sandbox_dir / ".git"
        if not git_dir.exists():
            subprocess.run(
                ["git", "init", "-q"],
                cwd=str(sandbox_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # Configure kaetram MCP server per-sandbox via CODEX_HOME isolation.
        # Each agent gets its own .codex/config.toml so MCP servers use the
        # correct port, username, and state directory.
        codex_home = sandbox_dir / ".codex"
        codex_home.mkdir(parents=True, exist_ok=True)
        self._codex_home = codex_home

        # Copy auth credentials from the real CODEX_HOME so the sandbox
        # can authenticate with OpenAI's API.
        real_codex_home = Path.home() / ".codex"
        real_auth = real_codex_home / "auth.json"
        if real_auth.exists():
            shutil.copy2(real_auth, codex_home / "auth.json")

        config_toml = f"""\
model = "{self.model}"
model_reasoning_effort = "medium"

[features]
codex_hooks = true

[mcp_servers.kaetram]
command = "{VENV_PYTHON}"
args = ["{PROJECT_DIR / 'mcp_game_server.py'}"]
tool_timeout_sec = 60
startup_timeout_sec = 30

[mcp_servers.kaetram.env]
KAETRAM_PORT = "{port}"
KAETRAM_USERNAME = "{username}"
KAETRAM_EXTRACTOR = "{PROJECT_DIR / 'state_extractor.js'}"
KAETRAM_STATE_DIR = "{sandbox_dir / 'state'}"

[projects."{sandbox_dir}"]
trust_level = "trusted"
"""
        (codex_home / "config.toml").write_text(config_toml)

        # Stop Hook: forces Codex to keep playing up to max_turns instead of
        # exiting after one turn. The hook intercepts the Stop event and
        # returns {"decision": "block"} to inject a continuation prompt.
        hook_script = PROJECT_DIR / "scripts" / "codex_stop_hook.py"
        hooks_json = {
            "hooks": {
                "Stop": [{
                    "hooks": [{
                        "type": "command",
                        "command": f"python3 {hook_script}",
                        "timeout": 10,
                    }],
                }],
            },
        }
        (codex_home / "hooks.json").write_text(json.dumps(hooks_json, indent=2))

        # Initialize turn counter (reset each session via start_session)
        (sandbox_dir / ".turn_counter").write_text("0")

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
        personality: str | None = None,
        session_n: int = 1,
    ) -> list[str]:
        # Stop hook forces continuation up to max_turns, but we still need a
        # timeout as a hard safety net. Add 5min buffer over the estimated time.
        timeout_seconds = max(max_turns * 30 + 300, 900)
        return [
            "timeout",
            str(timeout_seconds),
            "codex",
            "exec",
            user_prompt,
            "--model",
            self.model,
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
            "--enable", "codex_hooks",
            # Inject system prompt as developer instructions (stronger than AGENTS.md)
            "-c", f'model_instructions_file="system_prompt.md"',
        ]

    def get_env(self) -> dict[str, str]:
        env = {}
        # Isolate per-sandbox MCP config so each agent uses its own kaetram server
        if self._codex_home:
            env["CODEX_HOME"] = str(self._codex_home)
            # Stop hook reads these to track turns per session
            env["CODEX_TURN_COUNTER"] = str(self._codex_home.parent / ".turn_counter")
            env["CODEX_MAX_TURNS"] = "150"
        return env


class GeminiAdapter(CLIAdapter):
    """Adapter for Google Gemini CLI (gemini -p).

    Gemini CLI uses stream-json output (same format as Claude Code), MCP via
    .gemini/settings.json, and built-in maxSessionTurns for turn limits.
    No stop hook or timeout wrapper needed.
    """

    def __init__(self, model: str = "gemini-3-flash-preview"):
        super().__init__(model)

    @property
    def name(self) -> str:
        return "gemini"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        # Gemini discovers MCP config from .gemini/settings.json in cwd
        gemini_dir = sandbox_dir / ".gemini"
        gemini_dir.mkdir(parents=True, exist_ok=True)

        settings = {
            "mcpServers": {
                "kaetram": {
                    "command": VENV_PYTHON,
                    "args": [str(PROJECT_DIR / "mcp_game_server.py")],
                    "trust": True,
                    "env": {
                        "KAETRAM_PORT": port,
                        "KAETRAM_USERNAME": username,
                        "KAETRAM_EXTRACTOR": str(PROJECT_DIR / "state_extractor.js"),
                        "KAETRAM_STATE_DIR": str(sandbox_dir / "state"),
                    },
                },
            },
            "model": {
                "maxSessionTurns": 150,
            },
        }
        (gemini_dir / "settings.json").write_text(json.dumps(settings, indent=2))

        # System prompt via GEMINI.md (auto-discovered by Gemini CLI)
        if system_prompt:
            (gemini_dir / "GEMINI.md").write_text(system_prompt)

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
        personality: str | None = None,
        session_n: int = 1,
    ) -> list[str]:
        return [
            "gemini",
            "-p", user_prompt,
            "-m", self.model,
            "--output-format", "stream-json",
            "-y",  # yolo mode — auto-approve all tool calls
        ]

    def get_env(self) -> dict[str, str]:
        env = {}
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if api_key:
            env["GEMINI_API_KEY"] = api_key
        return env


class OpenCodeAdapter(CLIAdapter):
    """Adapter for OpenCode CLI (opencode run).

    OpenCode ships its own config schema (opencode.json) and reads system
    prompts from AGENTS.md in the run directory — same convention as Codex.
    Output format is --format json (not stream-json); each line is a JSON
    event with `type` and nested `part` / `message.content` shapes.

    We do not specify a model — opencode picks the default from the user's
    `opencode auth` configuration and the providers defined in
    opencode.template.json. The model field below is metadata-only.
    """

    def __init__(self, model: str | None = None):
        # Resolve shorthand (e.g. "qwen3.5-35a3b") to fully-qualified model
        # ID before storing. None / empty stays as the sentinel default so
        # setup_sandbox knows to leave the template's `model` field alone.
        resolved = resolve_opencode_model(model)
        super().__init__(resolved or "opencode-default")

    @property
    def name(self) -> str:
        return "opencode"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        # Resolve opencode.template.json into the sandbox — opencode reads
        # opencode.json from CWD (or $XDG_CONFIG_HOME/opencode/). The sandbox
        # CWD wins, so writing here keeps the kaetram MCP server scoped.
        template_path = PROJECT_DIR / "opencode.template.json"
        if not template_path.exists():
            raise RuntimeError(
                "opencode.template.json missing — add it at the project root "
                "with provider.ollama + mcp.kaetram entries"
            )
        # Build the per-agent config by injecting the MCP env block opencode
        # does not have placeholder substitution in its schema, so we load the
        # template as JSON and patch `mcp.kaetram.environment` directly.
        cfg = json.loads(template_path.read_text())
        # Apply --opencode-model override if one was supplied (otherwise
        # leave the template's default `model` field intact).
        if self.model and self.model != "opencode-default":
            cfg["model"] = self.model
        mcp = cfg.setdefault("mcp", {}).setdefault("kaetram", {})
        cmd = mcp.get("command", [])
        mcp["command"] = [
            VENV_PYTHON if c == "__VENV_PYTHON__" else c.replace("__PROJECT_DIR__", str(PROJECT_DIR))
            for c in cmd
        ]
        mcp["environment"] = {
            "KAETRAM_PORT":           port,
            "KAETRAM_USERNAME":       username,
            "KAETRAM_EXTRACTOR":      str(PROJECT_DIR / "state_extractor.js"),
            "KAETRAM_STATE_DIR":      str(sandbox_dir / "state"),
        }
        (sandbox_dir / "opencode.json").write_text(json.dumps(cfg, indent=2))
        # System prompt → AGENTS.md (opencode convention)
        if system_prompt:
            (sandbox_dir / "AGENTS.md").write_text(system_prompt)

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
        personality: str | None = None,
        session_n: int = 1,
    ) -> list[str]:
        # opencode run is one-shot per invocation — the outer play.sh loop
        # drives session cadence. We do NOT pass --model; opencode uses the
        # default provider/model from the user's opencode config + auth.
        timeout_seconds = max(max_turns * 45, 900)
        return [
            "timeout",
            str(timeout_seconds),
            "opencode",
            "run",
            "--format",
            "json",
            "--dangerously-skip-permissions",
            user_prompt,
        ]

    def get_env(self) -> dict[str, str]:
        # opencode reads auth per-provider from the opencode.json (Ollama
        # needs no auth; Modal/OpenAI pick up their keys from env automatically).
        return {}


# Modal endpoint resolution. The committed default workspace is the anonymized
# placeholder "workspace" — the repo is scrubbed of the real Modal workspace
# name for publication. On a live machine, set MODAL_WORKSPACE (e.g. in
# ~/.zshrc) so these resolve to the real deployment, or override the full URL
# via KAETRAM_QWEN_{SFT,BASE}_ENDPOINT. Leaving the placeholder unresolved
# produces `modal-http: invalid function call` on every request (silent 3h
# null-run failure mode — see session_log 2026-05-28).
_MODAL_WORKSPACE = os.environ.get("MODAL_WORKSPACE", "workspace")
QWEN_SFT_ENDPOINT = os.environ.get("KAETRAM_QWEN_SFT_ENDPOINT") or (
    f"https://{_MODAL_WORKSPACE}--kaetram-qwen-serve-inference-serve.modal.run/v1"
)
QWEN_BASE_ENDPOINT = os.environ.get("KAETRAM_QWEN_BASE_ENDPOINT") or (
    f"https://{_MODAL_WORKSPACE}--kaetram-qwen-base-inference-serve.modal.run/v1"
)
# Variant label for base runs. Defaults to the 9B base ("kaetram-base"); set
# KAETRAM_QWEN_BASE_MODEL when the base endpoint serves a different model (e.g.
# "kaetram-base-27b") so run.meta.json / dashboards / log_analysis report it.
QWEN_BASE_MODEL_LABEL = os.environ.get("KAETRAM_QWEN_BASE_MODEL", "kaetram-base")
# Variant label for SFT runs. Defaults to "r10-sft"; set KAETRAM_QWEN_SFT_MODEL
# when the SFT endpoint serves a different checkpoint (e.g. "2b-opd-r1") so
# run.meta.json / dashboards / log_analysis report the right variant.
QWEN_SFT_MODEL_LABEL = os.environ.get("KAETRAM_QWEN_SFT_MODEL", "r10-sft")


class QwenAdapter(CLIAdapter):
    """Adapter for the in-house Qwen3.5-9B model served on Modal SGLang.

    Wraps `play_qwen.py` as a LONG-LIVED warm-session subprocess. The Python
    process spans many sessions — MCPClient, Chromium browser, login, and
    Xvfb/ffmpeg all persist across context-overflow rollovers. play_qwen
    rotates `session_<N>_<TS>.log` files internally and writes
    `<sandbox>/state/.session_counter` itself. orchestrate only respawns
    play_qwen on hard process death (crash recovery), not on natural session
    boundaries.

    Variant labels: `model="r10-sft"` for the finetuned endpoint,
    `model="kaetram-base"` for the unfinetuned endpoint. The Modal endpoint
    serves whichever model is baked into the deployment, so the model name
    is a metadata label — but we keep it in lockstep with the endpoint URL
    so dashboards and run.meta.json never misreport the variant.

    Warm-session context (set by orchestrate / eval_harness before
    `build_command`):
        run_dir              : Path to write session_<N>_<TS>.log files
        harness_meta_path    : JSON file with sidecar template (per-agent fields)
        max_duration_seconds : Wall-clock cap (eval-only; 0 = unbounded)

    Solo-dev invocation can leave these unset — play_qwen falls back to
    `<sandbox>/logs` for the run_dir and runs unbounded.
    """

    def __init__(self, model: str = "r10-sft", endpoint: str | None = None):
        endpoint = endpoint or QWEN_SFT_ENDPOINT
        # Auto-correct the model label when the caller pointed at the base
        # endpoint but didn't override the (sft) default. Saves the dashboard
        # / log_analysis from showing "r10-sft" on a base run.
        if endpoint == QWEN_BASE_ENDPOINT and model == "r10-sft":
            model = QWEN_BASE_MODEL_LABEL
        super().__init__(model)
        self.endpoint = endpoint
        self._port: str = ""
        self._username: str = "QwenCompletionist"
        # Warm-session context — set by orchestrate / eval_harness before
        # build_command. None / 0 means "use play_qwen defaults".
        self.run_dir: Path | None = None
        self.harness_meta_path: Path | None = None
        self.max_duration_seconds: int = 0

    @property
    def name(self) -> str:
        return "qwen"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "QwenCompletionist") -> None:
        if system_prompt:
            (sandbox_dir / "system_prompt.md").write_text(system_prompt)
        self._port = port
        self._username = username

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,           # accepted for polymorphism; ignored
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
        personality: str | None = None,
        session_n: int = 1,       # accepted for polymorphism; ignored
    ) -> list[str]:
        # `user_prompt` and `session_n` are accepted to match the base-class
        # signature but ignored: play_qwen rebuilds the bootstrap per warm
        # session via the shared `bootstrap.build_orchestrate_bootstrap`,
        # using its own `.session_counter`. `max_turns` is also ignored —
        # warm-session lifetime is bounded by context-overflow rollovers and
        # (eval only) `--max-duration-seconds`.
        cmd = [
            VENV_PYTHON,
            str(PROJECT_DIR / "play_qwen.py"),
            "--endpoint", self.endpoint,
            "--model", self.model,
            "--system-prompt", "system_prompt.md",  # cwd-relative (orchestrate sets cwd to sandbox)
            "--sandbox", ".",
            "--project-dir", str(PROJECT_DIR),
        ]
        if self.run_dir is not None:
            cmd.extend(["--run-dir", str(self.run_dir)])
        if self.harness_meta_path is not None:
            cmd.extend(["--harness-meta", str(self.harness_meta_path)])
        if self.max_duration_seconds:
            cmd.extend(["--max-duration-seconds", str(self.max_duration_seconds)])
        if self._port:
            cmd.extend(["--server-port", str(self._port)])
        if personality:
            cmd.extend(["--personality", personality])
        return cmd

    def get_env(self) -> dict[str, str]:
        return {
            "PYTHONUNBUFFERED": "1",
            "KAETRAM_USERNAME": self._username,
        }


def get_adapter(harness: str = "claude", model: str | None = None,
                qwen_endpoint: str | None = None) -> CLIAdapter:
    """Factory function to create the appropriate CLI adapter.

    Args:
        harness: one of 'claude', 'codex', 'gemini', 'opencode', 'qwen'
        model: optional model override
        qwen_endpoint: optional Modal SGLang endpoint override (Qwen only)
    """
    if harness == "codex":
        return CodexAdapter(model=model or "gpt-5.4")
    elif harness == "gemini":
        return GeminiAdapter(model=model or "gemini-3-flash-preview")
    elif harness == "opencode":
        return OpenCodeAdapter(model=model)
    elif harness == "qwen":
        return QwenAdapter(model=model or QWEN_SFT_MODEL_LABEL, endpoint=qwen_endpoint)
    else:
        return ClaudeAdapter(model=model or "sonnet")


def detect_log_format(log_path: Path) -> str:
    """Detect CLI harness from session log format.

    Reads the first 25 JSON lines looking for format markers:
    - Claude: stream-json with claude_code_version
    - Codex: JSON with thread.started, item.completed events
    - Gemini: gemini_version or model contains "gemini"
    - OpenCode: tool_use events carry part.tool (no message.content), or
      step_finish events with part.tokens

    Returns 'claude', 'codex', 'gemini', 'opencode', or 'unknown'.
    """
    try:
        checked = 0
        with open(log_path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                checked += 1

                # Claude markers
                if obj.get("claude_code_version"):
                    return "claude"
                if obj.get("type") == "system" and obj.get("subtype") == "init":
                    return "claude"
                if obj.get("type") == "assistant" and "message" in obj:
                    msg = obj["message"]
                    if isinstance(msg, dict) and "content" in msg:
                        return "claude"

                # Gemini markers (stream-json like Claude but with gemini-specific init)
                model_str = str(obj.get("model", ""))
                if "gemini" in model_str:
                    return "gemini"
                if obj.get("gemini_version") or obj.get("client") == "gemini":
                    return "gemini"

                # Codex markers
                if obj.get("type") in ("thread.started", "turn.started",
                                        "item.started", "item.completed"):
                    return "codex"
                if "response_id" in obj or obj.get("type") == "response":
                    return "codex"
                if "role" in obj and "message" not in obj:
                    return "codex"
                if obj.get("event") in ("message", "function_call", "function_call_output"):
                    return "codex"

                # OpenCode markers — flat tool_use with part.tool (no nested
                # message.content), or step_finish events that carry
                # part.tokens token accounting.
                t = obj.get("type")
                part = obj.get("part") if isinstance(obj.get("part"), dict) else None
                if t == "tool_use" and part and part.get("tool") and "message" not in obj:
                    return "opencode"
                if t == "step_finish" and part and isinstance(part.get("tokens"), dict):
                    return "opencode"

                if checked >= 25:
                    break
    except OSError:
        pass
    return "unknown"
