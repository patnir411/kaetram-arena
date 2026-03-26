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

    # Pattern 1: <tool_call> tags
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

## HOW TO USE TOOLS — READ THIS CAREFULLY

You have exactly TWO tools. You MUST call them using <tool_call> XML tags. Do NOT use any other format.

### Tool 1: browser_run_code
Executes JavaScript in the game browser. This is your ONLY way to interact with the game.

<tool_call>
{"name": "browser_run_code", "arguments": {"code": "return window.__latestGameState"}}
</tool_call>

### Tool 2: Bash
Executes shell commands. Use ONLY for writing progress.json.

<tool_call>
{"name": "Bash", "arguments": {"command": "cat > state/progress.json << 'EOF'\n{}\nEOF"}}
</tool_call>

### RULES
- EVERY response MUST contain exactly ONE <tool_call> block
- The tool name is EXACTLY "browser_run_code" or "Bash" — no prefixes, no MCP__, no mcp__
- Do NOT use Node.js, require(), or any external libraries
- Do NOT describe what you want to do — just call the tool
- Do NOT use page.goto(), page.click(), or Playwright API — use browser_run_code with JavaScript that runs INSIDE the page
- To navigate: browser_run_code with code "window.location.href = 'http://localhost:9000'"
- To read state: browser_run_code with code "return JSON.stringify(window.__latestGameState)"
- To attack: browser_run_code with code "return window.__attackMob('Rat')"
- To login: browser_run_code with the login code from your system instructions"""

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
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        # Navigate to game immediately so the model doesn't have to
        print("Navigating to game client...")
        page.goto("http://localhost:9000", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(state_dir / "live_screen.png"))
        print("Game client loaded.")

        turn = 0
        consecutive_errors = 0

        while turn < args.max_turns:
            turn += 1

            # Screenshot before each turn
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

            # Take screenshot for dashboard after each tool dispatch round
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
    args = parser.parse_args()
    run_agent(args)


if __name__ == "__main__":
    main()
