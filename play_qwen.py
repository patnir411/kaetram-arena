#!/usr/bin/env python3
"""
play_qwen.py — Lightweight Claude-Code-like harness for finetuned Qwen3.5-9B.

Replicates Claude Code's behavior with just 2 tools (browser_run_code + Bash)
instead of OpenCode's 38. Multi-turn conversation with tool-call dispatch.

Usage:
    python3 play_qwen.py --endpoint https://your-modal-url/v1 \
        --system-prompt /path/to/system.md \
        --sandbox /tmp/kaetram_agent_4 \
        --username QwenBot
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Tool definitions (just 2 — matches training data format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browser_run_code",
            "description": "Execute JavaScript code in the game browser. Use helper functions: __attackMob(name), __interactNPC(name), __talkToNPC(id), __navigateTo(x,y), __moveTo(x,y), __clickEntity(label), __clickTile(x,y), __safeWarp(id), __eatFood(slot), __stuckReset(), __navCancel(). Read game state with window.__latestGameState. Return values provide action results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "JavaScript code to execute in the browser page context",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Execute a shell command. Use ONLY for writing progress.json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def parse_tool_calls_from_text(text: str) -> list[dict]:
    """Parse tool calls from model text output.

    Qwen3.5 tool format uses <tool_call> tags:
        <tool_call>
        {"name": "browser_run_code", "arguments": {"code": "..."}}
        </tool_call>

    Also handles: ✿TOOL_CALL✿ format and plain JSON function calls.
    """
    import re
    calls = []

    # Pattern 1a: Qwen3.5 XML format: <tool_call><function=name><parameter=key>value</parameter></function></tool_call>
    for m in re.finditer(r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>", text, re.DOTALL):
        fn_name = m.group(1)
        params_text = m.group(2)
        args = {}
        for pm in re.finditer(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", params_text, re.DOTALL):
            args[pm.group(1)] = pm.group(2)
        calls.append({"name": fn_name, "arguments": args})

    # Pattern 1b: Qwen3.5 shorthand: <tool_call><browser_run_code>code</browser_run_code></tool_call>
    if not calls:
        for m in re.finditer(r"<tool_call>\s*<(browser_run_code|Bash)>(.*?)</\1>\s*</tool_call>", text, re.DOTALL):
            fn_name = m.group(1)
            inner = m.group(2).strip()
            if fn_name == "browser_run_code":
                calls.append({"name": fn_name, "arguments": {"code": inner}})
            else:
                calls.append({"name": fn_name, "arguments": {"command": inner}})

    # Pattern 1c: JSON inside <tool_call> tags
    if not calls:
        for m in re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL):
            try:
                tc = json.loads(m.group(1))
                calls.append(tc)
            except json.JSONDecodeError:
                pass

    # Pattern 2: ✿TOOL_CALL✿ format
    for m in re.finditer(r"✿TOOL_CALL✿\s*(.*?)(?=✿|$)", text, re.DOTALL):
        try:
            tc = json.loads(m.group(1).strip())
            calls.append(tc)
        except json.JSONDecodeError:
            pass

    # Pattern 3: {"name": "browser_run_code", ...} JSON in text
    if not calls:
        for m in re.finditer(r'\{"name"\s*:\s*"(browser_run_code|Bash)".*?\}', text, re.DOTALL):
            try:
                # Find the full JSON object
                start = m.start()
                depth = 0
                for i in range(start, len(text)):
                    if text[i] == "{": depth += 1
                    elif text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            tc = json.loads(text[start:i+1])
                            calls.append(tc)
                            break
            except json.JSONDecodeError:
                pass

    return calls


def dispatch_browser_run_code(page, code: str) -> str:
    """Execute browser_run_code — handles both raw JS and Playwright MCP format.

    The model outputs Playwright MCP format: async (page) => { page.evaluate(...) }
    We unwrap page.evaluate() calls and execute the inner JS directly.
    We also handle page.goto(), page.waitForTimeout(), page.screenshot() natively.
    """
    import re
    results = []

    # Strip the async (page) => { ... } wrapper if present
    stripped = code.strip()
    if stripped.startswith("async"):
        # Remove outer function wrapper
        m = re.match(r"async\s*\(page\)\s*=>\s*\{(.*)\}\s*$", stripped, re.DOTALL)
        if m:
            stripped = m.group(1).strip()

    # Handle page.goto()
    for m in re.finditer(r"page\.goto\(['\"]([^'\"]+)['\"]\)", stripped):
        url = m.group(1)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            results.append(f"Navigated to {url}")
        except Exception as e:
            results.append(f"goto error: {e}")

    # Handle page.waitForTimeout()
    for m in re.finditer(r"page\.waitForTimeout\((\d+)\)", stripped):
        ms = min(int(m.group(1)), 10000)
        page.wait_for_timeout(ms)
        results.append(f"Waited {ms}ms")

    # Handle page.screenshot() — we do this automatically, skip model's calls
    if "page.screenshot" in stripped:
        results.append("Screenshot handled by harness")

    # Handle page.evaluate() calls — extract inner JS and run it
    for m in re.finditer(
        r"page\.evaluate\(\s*(?:\([^)]*\)\s*=>)?\s*(.+?)(?:\)|,\s*['\"])", stripped, re.DOTALL
    ):
        inner_js = m.group(1).strip()
        # Clean up: remove trailing ) or quotes
        inner_js = re.sub(r"['\"]?\s*\)?\s*;?\s*$", "", inner_js)
        if inner_js:
            try:
                result = page.evaluate(inner_js)
                r = json.dumps(result) if result is not None else "undefined"
                results.append(r)
            except Exception as e:
                results.append(f"evaluate error: {e}")

    # Handle page.locator().fill() for login forms
    for m in re.finditer(r"page\.locator\(['\"]([^'\"]+)['\"]\)\.fill\(['\"]([^'\"]+)['\"]\)", stripped):
        selector, value = m.group(1), m.group(2)
        try:
            page.locator(selector).fill(value)
            results.append(f"Filled {selector}")
        except Exception as e:
            results.append(f"fill error: {e}")

    # Handle page.locator().click()
    for m in re.finditer(r"page\.locator\(['\"]([^'\"]+)['\"]\)\.click\(\)", stripped):
        selector = m.group(1)
        try:
            page.locator(selector).click()
            results.append(f"Clicked {selector}")
        except Exception as e:
            results.append(f"click error: {e}")

    # If nothing was matched, try executing as raw browser JS
    if not results:
        try:
            if "await" in stripped:
                wrapped = f"(async () => {{ {stripped} }})()"
            else:
                wrapped = f"(() => {{ {stripped} }})()"
            result = page.evaluate(wrapped)
            return json.dumps(result) if result is not None else "undefined"
        except Exception as e:
            return f"Error: {e}"

    return "\n".join(results)


def dispatch_bash(command: str) -> str:
    """Execute a shell command."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"
        return output[:2000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out (30s)"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _dispatch(page, fn_name: str, fn_args: dict, turn: int) -> str:
    """Dispatch a tool call and return the result."""
    if fn_name == "browser_run_code":
        code = fn_args.get("code", "")
        print(f"  [{turn}] browser_run_code: {code[:80]}...")
        return dispatch_browser_run_code(page, code)
    elif fn_name == "Bash":
        command = fn_args.get("command", "")
        print(f"  [{turn}] Bash: {command[:80]}...")
        return dispatch_bash(command)
    else:
        print(f"  [{turn}] Unknown tool: {fn_name}")
        return f"Unknown tool: {fn_name}"


def log_turn(log_file, turn: int, role: str, content: str, tool_calls=None):
    """Append a turn record to the session log."""
    record = {
        "turn": turn,
        "timestamp": datetime.now().isoformat(),
        "role": role,
        "content": content[:500] if content else "",
    }
    if tool_calls:
        record["tool_calls"] = [
            {"name": tc.function.name, "args": tc.function.arguments[:200]}
            for tc in tool_calls
        ]
    with open(log_file, "a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def run_agent(args):
    sandbox = Path(args.sandbox)
    state_dir = sandbox / "state"
    log_dir = sandbox / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Session log
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"session_{timestamp}.log"

    # Init OpenAI client
    client = OpenAI(base_url=args.endpoint, api_key=args.api_key or "not-needed", timeout=300)

    # Load system prompt
    system_prompt = ""
    if args.system_prompt and os.path.isfile(args.system_prompt):
        system_prompt = open(args.system_prompt).read()
    elif args.system_prompt:
        system_prompt = args.system_prompt

    # Append tool-use instructions to system prompt since SGLang doesn't
    # process the tools parameter for structured tool_calls in responses.
    # The model must output <tool_call> tags in its text response.
    tool_instructions = """

## HOW TO USE TOOLS

You have TWO tools. Call them using <tool_call> XML tags.

### Tool 1: browser_run_code (USE THIS 95% OF THE TIME)
Executes JavaScript in the game browser. This is your ONLY way to interact with the game.

### Tool 2: Bash (USE SPARINGLY — only every 20+ turns)
Write progress.json. Do NOT use Bash for anything else. Do NOT call Bash multiple times in a row.

### GAMEPLAY LOOP (follow this exactly)
1. OBSERVE: `return JSON.stringify(window.__latestGameState)` — do this first every few turns
2. DECIDE: Based on game state, pick ONE action
3. ACT: Call the appropriate helper function

### HELPER FUNCTIONS (use these, not raw JS)
- `return window.__attackMob('Rat')` — attack nearest mob by name
- `return window.__navigateTo(188, 157)` — pathfind to coordinates
- `return window.__moveTo(x, y)` — short move (<15 tiles)
- `return window.__interactNPC('Villager')` — walk to and talk to NPC
- `return window.__talkToNPC('1-12345')` — advance NPC dialogue (use instance id)
- `return window.__safeWarp(0)` — warp (0=Mudwich, 1=Crossroads, 2=Lakesworld)
- `return window.__eatFood(slot)` — eat food at inventory slot
- `return window.__clickTile(x, y)` — click a grid tile
- `return window.__stuckReset()` — reset if stuck

### RULES
- EVERY response MUST contain exactly ONE <tool_call> block
- ALWAYS use browser_run_code for game actions — Bash is ONLY for progress.json
- If you call Bash more than once in 5 turns, STOP and use browser_run_code instead
- Do NOT use page.goto(), page.click(), or Playwright API
- Do NOT call __login() — login is handled automatically"""

    system_prompt = system_prompt + tool_instructions

    # Build initial messages
    messages = [{"role": "system", "content": system_prompt}]
    if args.user_prompt:
        messages.append({"role": "user", "content": args.user_prompt})

    print(f"Harness started: {args.max_turns} max turns, endpoint={args.endpoint}")
    print(f"Log: {log_file}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1280, "height": 720})

        # Inject state_extractor.js (provides __latestGameState, __attackMob, etc.)
        extractor_path = os.path.join(args.project_dir, "state_extractor.js")
        if os.path.exists(extractor_path):
            context.add_init_script(path=extractor_path)
            print(f"Injected {extractor_path}")

        # WebSocket port override for multi-agent isolation
        if args.server_port:
            context.add_init_script(f"""(() => {{
                const PORT = '{args.server_port}';
                const _WS = window.WebSocket;
                window.WebSocket = function(url, protocols) {{
                    url = url.replace(/\\/\\/[^:/]+/, '//localhost');
                    url = url.replace(/:9001(?=\\/|$)/, ':' + PORT);
                    return protocols ? new _WS(url, protocols) : new _WS(url);
                }};
                window.WebSocket.prototype = _WS.prototype;
                window.WebSocket.CONNECTING = 0; window.WebSocket.OPEN = 1;
                window.WebSocket.CLOSING = 2; window.WebSocket.CLOSED = 3;
            }})()""")
            print(f"WebSocket port override: {args.server_port}")

        page = context.new_page()

        # Navigate to game and auto-login
        username = os.environ.get("KAETRAM_USERNAME", "QwenBot")
        print(f"Navigating to game client, logging in as {username}...")
        page.goto("http://localhost:9000", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Login flow (same as mcp_game_server.py)
        page.locator("#login-name-input").fill(username)
        page.locator("#login-password-input").fill("password123")
        page.locator("#login").click()
        page.wait_for_timeout(4000)

        # Check if we need to register (account doesn't exist)
        still_on_login = page.evaluate("""() => {
            const el = document.getElementById('load-character');
            if (!el) return false;
            const s = window.getComputedStyle(el);
            return s.display !== 'none' && s.opacity !== '0';
        }""")
        if still_on_login:
            page.evaluate(f"""(username) => {{
                document.getElementById('new-account').click();
                setTimeout(() => {{
                    const set = (el, val) => {{
                        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
                            .set.call(el, val);
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    }};
                    set(document.getElementById('register-name-input'), username);
                    set(document.getElementById('register-password-input'), 'password123');
                    set(document.getElementById('register-password-confirmation-input'), 'password123');
                    set(document.getElementById('register-email-input'), username + '@test.com');
                    setTimeout(() => document.getElementById('play').click(), 300);
                }}, 500);
            }}""", username)
            page.wait_for_timeout(8000)

        page.wait_for_timeout(2000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)

        # Verify game loaded
        game_ready = False
        for _attempt in range(3):
            game_ready = page.evaluate(
                "() => !!(window.game && window.game.player && typeof window.game.player.gridX === 'number')"
            )
            if game_ready:
                break
            page.wait_for_timeout(3000)

        if not game_ready:
            print(f"Login FAILED for {username} — game did not load")
            browser.close()
            return

        page.screenshot(path=str(state_dir / "live_screen.png"))
        print(f"Logged in as {username}, game loaded.")

        turn = 0
        consecutive_errors = 0

        while turn < args.max_turns:
            turn += 1

            # Screenshot for dashboard MJPEG stream
            try:
                page.screenshot(path=str(state_dir / "live_screen.png"))
            except Exception:
                pass

            # Call model
            try:
                response = client.chat.completions.create(
                    model=args.model,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=2048,
                )
                choice = response.choices[0]
                consecutive_errors = 0
            except Exception as e:
                print(f"  [{turn}] API error: {e}")
                consecutive_errors += 1
                if consecutive_errors > 3:
                    print("Too many API errors, stopping.")
                    break
                time.sleep(5)
                continue

            # Log assistant response
            content = choice.message.content or ""
            tool_calls = choice.message.tool_calls

            if content:
                print(f"  [{turn}] Assistant: {content[:120]}...")
                log_turn(log_file, turn, "assistant", content, tool_calls)

            # Check for structured tool_calls from API
            if tool_calls:
                messages.append(choice.message.model_dump())
                for tc in tool_calls:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        fn_args = {"code": tc.function.arguments} if fn_name == "browser_run_code" else {"command": tc.function.arguments}

                    result = _dispatch(page, fn_name, fn_args, turn)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result[:4000]})
                    log_turn(log_file, turn, "tool", f"{fn_name}: {result[:200]}")

            # No structured tool_calls — check for text-based tool calls
            elif content:
                text_calls = parse_tool_calls_from_text(content)
                if text_calls:
                    messages.append({"role": "assistant", "content": content})
                    for tc_dict in text_calls:
                        fn_name = tc_dict.get("name", "")
                        fn_args = tc_dict.get("arguments", {})
                        if isinstance(fn_args, str):
                            try: fn_args = json.loads(fn_args)
                            except: fn_args = {}

                        result = _dispatch(page, fn_name, fn_args, turn)
                        # For text-based calls, append result as user message
                        messages.append({"role": "user", "content": f"Tool result ({fn_name}):\n{result[:4000]}"})
                        log_turn(log_file, turn, "tool", f"{fn_name}: {result[:200]}")
                else:
                    # Pure text, no tool calls — model is just reasoning
                    messages.append({"role": "assistant", "content": content})
                    if choice.finish_reason == "stop":
                        print(f"  [{turn}] Model stopped (no tool call). Continuing...")
                        time.sleep(2)

            # Screenshot after tool dispatch
            try:
                page.screenshot(path=str(state_dir / "live_screen.png"))
            except Exception:
                pass

            # Save game state for dashboard (try to extract from page)
            try:
                gs = page.evaluate("JSON.stringify(window.__latestGameState || {})")
                if gs and gs != "{}":
                    (state_dir / "game_state.json").write_text(gs)
            except Exception:
                pass

            # Context window management: trim old messages if too many
            if len(messages) > 60:
                # Keep system + last 40 messages
                messages = messages[:1] + messages[-40:]

        print(f"\nSession complete: {turn} turns, log: {log_file}")
        browser.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Kaetram Qwen agent harness")
    parser.add_argument("--endpoint", required=True, help="OpenAI-compatible API base URL")
    parser.add_argument("--model", default="kaetram", help="Model name")
    parser.add_argument("--api-key", default=None, help="API key (default: not-needed)")
    parser.add_argument("--system-prompt", default=None, help="System prompt file or text")
    parser.add_argument("--user-prompt", default=None, help="Initial user message")
    parser.add_argument("--sandbox", default="/tmp/kaetram_agent_4", help="Sandbox directory")
    parser.add_argument("--max-turns", type=int, default=300, help="Max conversation turns")
    parser.add_argument("--server-port", default="", help="Game server WebSocket port (e.g. 9031)")
    parser.add_argument("--project-dir", default=os.path.dirname(os.path.abspath(__file__)), help="Project directory (for state_extractor.js)")
    args = parser.parse_args()
    run_agent(args)


if __name__ == "__main__":
    main()
