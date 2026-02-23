# Kaetram AI Agent

An autonomous AI agent that plays [Kaetram](https://github.com/Kaetram/Kaetram-Open), a 2D pixel MMORPG, using Claude Code (Sonnet) and Playwright browser automation.

The agent logs into the game as a guest, navigates the world, fights monsters, talks to NPCs, completes quests, and plays alongside human players — all through screenshot-based vision and keyboard/mouse input.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  play.sh     │────>│ Claude Code  │────>│  Playwright   │
│  (loop)      │     │  (Sonnet)    │     │  (browser)    │
└──────────────┘     └──────┬───────┘     └──────┬────────┘
                            │                     │
                    reads prompts/          interacts with
                    writes state/          http://localhost:9000
                            │                     │
                     ┌──────┴───────┐     ┌───────┴───────┐
                     │  state/      │     │   Kaetram     │
                     │  progress    │     │   Game Server  │
                     │  screenshots │     │   (Node.js)   │
                     └──────────────┘     └───────────────┘
                            │
                     ┌──────┴───────┐
                     │  dashboard   │
                     │  :8080       │
                     └──────────────┘
```

**play.sh** runs an infinite loop, launching Claude Code sessions. Each session:
1. Reads the system prompt and previous game state
2. Claude controls a headless browser via Playwright MCP
3. Takes screenshots to "see" the game, sends keyboard/mouse input to play
4. Writes progress to `state/progress.json` after each session

**dashboard.py** serves a live web dashboard on port 8080 showing the agent's screenshots, game state, and session logs.

## Prerequisites

- **Claude Code** (`claude`) installed globally via npm
- **Playwright MCP** configured in Claude Code for browser automation
- **Kaetram-Open** game server cloned and runnable (see [Kaetram-Open](https://github.com/Kaetram/Kaetram-Open))
- **Node.js 20** (via nvm) — required for Kaetram server; Node 24 breaks uWS.js
- **Python 3** — for the dashboard

## Setup

### 1. Start the Kaetram game server

```bash
./scripts/start-kaetram.sh
```

Or manually:

```bash
source ~/.nvm/nvm.sh && nvm use 20
cd /path/to/Kaetram-Open
ACCEPT_LICENSE=true SKIP_DATABASE=true yarn start
```

The game client serves on **port 9000** and the WebSocket game server on **port 9001**.

**Recommended server `.env` settings** (in Kaetram-Open root):
```
ACCEPT_LICENSE=true
SKIP_DATABASE=true
TUTORIAL_ENABLED=false
```

Setting `SKIP_DATABASE=true` runs without MongoDB and grants admin commands to all players (useful for `/teleport`). Setting `TUTORIAL_ENABLED=false` skips the multi-stage tutorial quest.

### 2. Start the dashboard (optional)

```bash
python3 dashboard.py
```

Opens a live monitoring dashboard at `http://localhost:8080` with auto-refreshing screenshots, player stats, and session logs.

### 3. Run the agent

```bash
./play.sh
```

This starts the autonomous gameplay loop. Each session runs Claude Code (Sonnet) with 25 turns max, then pauses 10 seconds before the next session.

## Project Structure

```
kaetram-agent/
├── play.sh                  # Main agent loop — runs Claude Code sessions
├── dashboard.py             # Live web dashboard (port 8080)
├── prompts/
│   └── system.md            # System prompt: game controls, strategy, tasks
├── scripts/
│   ├── start-kaetram.sh     # Starts the Kaetram game server
│   ├── format-vertical.sh   # Converts clips to 9:16 vertical format
│   └── cut-highlight.sh     # Extracts highlight clips from recordings
├── state/
│   └── progress.json        # Current session state (updated by agent)
├── logs/                    # Session execution logs (JSON)
├── recordings/              # Full session recordings
└── highlights/              # Highlight video clips
```

## How It Works

### Agent Vision
The agent sees the game exclusively through screenshots taken via Playwright. It describes what it sees (HP bars, nearby entities, terrain) and decides on actions.

### Controls
- **Movement**: WASD keys (hold-to-move, not tap). The agent holds a key down for N seconds to walk continuously.
- **Combat**: Click on monsters to auto-attack.
- **NPCs**: Click on NPCs to interact. Blue `!` marks indicate available quests.
- **Chat**: Press Enter to open chat, type messages or commands, Enter to send.
- **Inventory**: Press `I` to toggle inventory.

### Spawn & Navigation
New guest players spawn inside the Programmer's house in Mudwich village. The exit door is gated behind the tutorial quest. The agent bypasses this using the admin teleport command:

```
/teleport 188 157
```

This teleports directly to the Mudwich village center (outdoors). This works because `SKIP_DATABASE=true` grants admin privileges to all players.

### Key Coordinates

| Location | Coordinates | Notes |
|----------|-------------|-------|
| Mudwich village center | 188, 157 | Main starting area, NPCs, quests |
| Default spawn point | 328, 892 | Programmer's house entrance |
| Tutorial dungeon spawn | 133, 562 | Tutorial starting room |

## Lessons Learned

### Playwright MCP Gotchas
- **Screenshot paths must be absolute.** Using relative paths (e.g., `state/screenshot.png`) causes the Playwright MCP to navigate the browser to the screenshot URL, losing the game page entirely.
- **WASD is hold-to-move.** The game sets `player.moveDown = true` on keydown and `false` on keyup. Use `page.keyboard.down('s')` + wait + `page.keyboard.up('s')`.
- **Keep all actions in `browser_run_code` blocks** to avoid the browser page being garbage collected between tool calls.

### Kaetram Game Gotchas
- **Tutorial-gated doors**: The starting room exit requires completing a 16-stage tutorial quest. Even with `TUTORIAL_ENABLED=false`, the `isTutorialFinished()` check still returns false for new guests. Workaround: `/teleport`.
- **Node.js version**: Kaetram uses uWS.js which only supports Node 16/18/20. Node 24 crashes on startup.
- **Port conflicts**: If the server is restarted without killing old processes, the client binds to a random port instead of 9000.

## Dashboard

The dashboard (`dashboard.py`) provides a dark terminal-themed web UI at port 8080:

- **Player Status**: Name, level, HP/MP, current location
- **Session Info**: Session number, game version, active events
- **Screenshot Gallery**: 20 most recent captures with lightbox view
- **Observations**: JSON view of latest gameplay observations
- **Auto-refresh**: Updates every 10 seconds

## Video Tools

Two helper scripts for creating highlight content:

- **`scripts/format-vertical.sh`** — Converts 16:9 clips to 9:16 vertical format with text overlay (for social media)
- **`scripts/cut-highlight.sh`** — Extracts the last N seconds from a recording as an MP4 clip

## License

This project is a tooling layer around [Kaetram-Open](https://github.com/Kaetram/Kaetram-Open) (MPL-2.0) and [Claude Code](https://claude.ai/claude-code) by Anthropic.
