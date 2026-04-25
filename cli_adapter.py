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
    screenshot_dir = str(sandbox_dir / "state")
    return (text
            .replace("__VENV_PYTHON__", VENV_PYTHON)
            .replace("__PROJECT_DIR__", str(PROJECT_DIR))
            .replace("__SCREENSHOT_DIR__", screenshot_dir)
            .replace("__SERVER_PORT__", str(port))
            .replace("__USERNAME__", username)
            )


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
    ) -> list[str]:
        """Build the CLI command to launch an agent session."""

    @abstractmethod
    def get_env(self) -> dict[str, str]:
        """Extra environment variables for the subprocess."""

    def parse_game_state_from_log(self, log_path: Path) -> str | None:
        """Extract the last game state JSON from a session log file.

        Searches for lines containing both 'player_position' and
        'nearby_entities', extracts the game state, and truncates large arrays.
        Returns a compact JSON string or None.
        """
        try:
            size = log_path.stat().st_size
            tail_size = min(size, 1_048_576)  # read last 1MB
            with open(log_path, "rb") as f:
                if size > tail_size:
                    f.seek(size - tail_size)
                data = f.read().decode("utf-8", errors="replace")

            last_state = None
            for line in data.splitlines():
                if "player_position" in line and "nearby_entities" in line:
                    state_text = self._extract_state_text_from_line(line)
                    if state_text:
                        last_state = state_text

            if not last_state:
                return None

            d = json.loads(last_state)
            d["nearby_entities"] = d.get("nearby_entities", [])[:15]
            d["inventory"] = d.get("inventory", [])[:15]
            d["quests"] = d.get("quests", [])[:10]
            d["achievements"] = d.get("achievements", [])[:10]
            return json.dumps(d, separators=(",", ":"))
        except (OSError, json.JSONDecodeError):
            return None

    @abstractmethod
    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Extract game state JSON string from a single log line.

        Each CLI produces different JSON wrappers around tool results.
        Subclasses implement format-specific extraction.
        """


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

    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Claude stream-json: state is in message.content[].text."""
        try:
            obj = json.loads(line)
            for block in obj.get("message", {}).get("content", []):
                text = block.get("text", "") if isinstance(block, dict) else ""
                if "player_position" in text and "nearby_entities" in text:
                    return text
        except (json.JSONDecodeError, AttributeError):
            pass
        return None


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
        # correct port, username, and screenshot directory.
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
KAETRAM_SCREENSHOT_DIR = "{sandbox_dir / 'state'}"

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

    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Codex --json: search for game state in various event structures.

        Codex emits item.started/item.completed events with mcp_tool_call items.
        Game state appears in item.result.content[].text on item.completed events.
        """
        try:
            obj = json.loads(line)

            # Primary: item.completed with result.content[].text
            if obj.get("type") == "item.completed":
                item = obj.get("item", {})
                result = item.get("result", {})
                if isinstance(result, dict):
                    for block in result.get("content", []):
                        text = block.get("text", "") if isinstance(block, dict) else ""
                        if "player_position" in text and "nearby_entities" in text:
                            return text

            # Fallback: message.content[] (older format)
            for block in obj.get("message", {}).get("content", []):
                text = block.get("text", "") if isinstance(block, dict) else ""
                if "player_position" in text and "nearby_entities" in text:
                    return text

            # Fallback: top-level output/result string
            output = obj.get("output", "") or obj.get("result", "")
            if isinstance(output, str) and "player_position" in output and "nearby_entities" in output:
                return output

        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        # Last resort: raw substring extraction
        if "player_position" not in line or "nearby_entities" not in line:
            return None
        try:
            start = line.find('{"player_position"')
            if start == -1:
                start = line.find('"player_position"')
                if start > 0:
                    brace = line.rfind("{", 0, start)
                    if brace != -1:
                        start = brace
                    else:
                        return None

            if start != -1:
                depth = 0
                for i in range(start, len(line)):
                    if line[i] == "{":
                        depth += 1
                    elif line[i] == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = line[start : i + 1]
                            parsed = json.loads(candidate)
                            if "player_position" in parsed and "nearby_entities" in parsed:
                                return candidate
                            break
        except (json.JSONDecodeError, ValueError):
            pass

        return None


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
                        "KAETRAM_SCREENSHOT_DIR": str(sandbox_dir / "state"),
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

    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Gemini stream-json: state is in tool_result.output (flat events)."""
        try:
            obj = json.loads(line)
            # Gemini tool_result: {"type": "tool_result", "output": "...game state..."}
            if obj.get("type") == "tool_result":
                output = obj.get("output", "")
                if isinstance(output, str) and "player_position" in output and "nearby_entities" in output:
                    return output
        except (json.JSONDecodeError, AttributeError):
            pass
        return None


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
        super().__init__(model or "opencode-default")

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
            "KAETRAM_SCREENSHOT_DIR": str(sandbox_dir / "state"),
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

    def _extract_state_text_from_line(self, line: str) -> str | None:
        """opencode --format json: each line is a JSON event. Tool results
        land in `part.state.output` as a JSON string containing the raw
        MCP tool response. We look for observe output with the game state
        signature."""
        try:
            obj = json.loads(line)
            part = obj.get("part", {})
            if obj.get("type") == "tool_use":
                state = part.get("state", {})
                output = state.get("output")
                if isinstance(output, str) and "player_position" in output and "nearby_entities" in output:
                    return output
            # Some opencode builds emit assistant-authored state echoes in text events
            for block in obj.get("message", {}).get("content", []):
                text = block.get("text", "") if isinstance(block, dict) else ""
                if "player_position" in text and "nearby_entities" in text:
                    return text
        except (json.JSONDecodeError, AttributeError):
            pass
        return None


def get_adapter(harness: str = "claude", model: str | None = None) -> CLIAdapter:
    """Factory function to create the appropriate CLI adapter.

    Args:
        harness: one of 'claude', 'codex', 'gemini', 'opencode'
        model: optional model override
    """
    if harness == "codex":
        return CodexAdapter(model=model or "gpt-5.4")
    elif harness == "gemini":
        return GeminiAdapter(model=model or "gemini-3-flash-preview")
    elif harness == "opencode":
        return OpenCodeAdapter(model=model)
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
