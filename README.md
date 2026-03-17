# Kaetram AI Agent

An autonomous AI agent that plays [Kaetram](https://github.com/Kaetram/Kaetram-Open), a 2D pixel MMORPG, using Claude Code (Sonnet) and Playwright browser automation. The agent plays the game, collects structured training data, and builds a dataset for distilling into a smaller vision-language model.

## What it does

- Logs in as ClaudeBot, navigates the world, fights monsters, loots drops, talks to NPCs
- Reads real-time game state (nearby entities, combat events, XP) from the WebSocket server
- Records every action as a `(screenshot, game_state, action, reward)` tuple for training
- Runs indefinitely in sessions — each session picks up where the last left off

## Architecture

```
play.sh ──────────► Claude Code (Sonnet) ──────► Playwright MCP ──► browser @ localhost:9000
                          │                                                    │
                    reads/writes                                       Kaetram game server
                    state/, prompts/                                   WebSocket @ 9001
                          │                                                    │
                          └──────────────────────────────────────────────────►│
                                                                               │
                                                              ws_observer.py ◄─┘
                                                              writes state/game_state.json
                                                                     │
                                                              logger.py ◄── watches screenshot mtime
                                                              writes dataset/session_N/steps.jsonl
```

**`play.sh`** — infinite loop, launches Claude Code sessions (100 turns max, 10s pause between)

**`ws_observer.py`** — connects to the Kaetram WebSocket as a guest, maintains `state/game_state.json` with nearby entities (name, type, x/y tile coords, HP), combat events, XP gains

**`logger.py`** — watches `state/screenshot.png` for changes, records one step per screenshot into `dataset/session_N/steps.jsonl` with the full game state at that moment

**`dashboard.py`** — live web UI at port 8080, shows screenshots, entity list, session log

**`prompts/system.md`** — the system prompt Claude reads every session: phase-by-phase instructions, how to read game state, how to target mobs by coordinate

## Quick start

Run each in its own terminal, in order:

```bash
# Terminal 1 — Kaetram game server (Node 20 required)
./scripts/start-kaetram.sh

# Terminal 2 — WebSocket observer (feeds real-time entity data)
python3 ws_observer.py

# Terminal 3 — Dataset logger (records training steps)
python3 logger.py

# Terminal 4 — Dashboard (optional, live monitoring)
python3 dashboard.py

# Terminal 5 — Agent loop (must be a separate terminal — see gotchas)
./play.sh
```

> ⚠️ **`play.sh` must always be in its own terminal.** Running it as a subprocess of Claude Code deadlocks both processes on the shared Playwright MCP browser.

## Project structure

```
kaetram-arena/
├── play.sh                  # Agent loop — launches Claude Code sessions
├── ws_observer.py           # WebSocket observer — writes state/game_state.json
├── logger.py                # Dataset logger — writes dataset/session_N/steps.jsonl
├── dashboard.py             # Live web dashboard (port 8080)
├── prompts/
│   └── system.md            # System prompt: phases, game controls, targeting strategy
├── scripts/
│   ├── start-kaetram.sh     # Starts Kaetram server (handles nvm)
│   ├── cut-highlight.sh     # Extracts highlight clips from recordings
│   └── format-vertical.sh  # Converts clips to 9:16 vertical format
├── tests/
│   ├── test_ws_observer.py  # 21 unit tests for ws_observer
│   └── test_logger.py       # Simulated 5-turn logger test
├── .claude/
│   └── commands/            # Claude Code slash commands
│       ├── game-session.md  # /game-session — check stack status, startup guide
│       ├── verify-pipeline.md # /verify-pipeline — health check, inspect training records
│       └── training-summary.md # /training-summary — dataset stats, reward trends
├── state/                   # Runtime state (gitignored)
│   ├── screenshot.png       # Current game view (written by Claude)
│   └── game_state.json      # Live entity/combat/XP data (written by ws_observer)
├── dataset/                 # Training data
│   └── session_N/
│       ├── steps.jsonl      # (screenshot, state, action, reward) per step
│       └── frames/          # Screenshot files (gitignored — large)
├── logs/                    # Claude Code session logs (JSONL)
├── session_log.md           # Running log of decisions and context across sessions
└── CLAUDE.md                # Project instructions for Claude Code
```

## Training data format

Each line in `steps.jsonl` is one agent step:

```json
{
  "session": 1,
  "step": 12,
  "timestamp": 1234567890.0,
  "screenshot": "/abs/path/to/frame_012.png",
  "state": {
    "nearby_entities": [
      {"id": "3-162044154", "type": 3, "name": "Rat", "x": 371, "y": 866, "hp": 20, "max_hp": 20}
    ],
    "last_combat": null,
    "last_xp_event": {"amount": 40, "skill": 17, "level": null},
    "player_count_nearby": 0
  },
  "action": "attacked Rat at (371, 866)",
  "reward": 1.0,
  "done": false
}
```

Entity types: `1` = NPC, `3` = mob, `4` = item drop.

## Slash commands

Three built-in skills for managing the project:

| Command | When to use |
|---------|-------------|
| `/game-session` | Check what's running, get startup commands, see port status |
| `/verify-pipeline` | Confirm data is flowing, inspect latest training record |
| `/training-summary` | Dataset stats, reward trends, best/worst sessions |

## Gotchas

**Playwright subprocess deadlock** — `play.sh` must run in a separate terminal. Spawning it as a subprocess of Claude Code deadlocks both on the shared Playwright MCP browser.

**ws_observer spawn offset** — `ws_observer.py` connects as its own guest at the default spawn (~tile 328, 892). Its `nearby_entities` reflects mobs near that location, not where ClaudeBot actually is (Mudwich, 188/157). Known limitation.

**Node 20 required** — Kaetram uses uWS.js which only supports Node 16/18/20. Node 24/25 crashes on startup.

**Tutorial gate** — New players spawn in the Programmer's house behind a 16-stage tutorial. Workaround: send `/teleport 188 157` in chat immediately after login.

**Absolute screenshot paths** — Playwright MCP requires absolute paths. Relative paths cause it to navigate the browser to the path as a URL.

## Tests

```bash
python3 tests/test_ws_observer.py   # 21 unit tests — no live server needed
python3 tests/test_logger.py        # Simulated 5-turn logger test
```

## What's next

- Fix ws_observer spawn offset so `nearby_entities` reflects ClaudeBot's actual area
- Accumulate training data across sessions
- Distillation pipeline: fine-tune Qwen 2.5 VL 7B on `(screenshot, game_state, action)` tuples

## License

Tooling layer around [Kaetram-Open](https://github.com/Kaetram/Kaetram-Open) (MPL-2.0).
