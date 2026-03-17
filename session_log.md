# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-03-17 — PR3 live verification + skills system

**Built:**
- PR3: `play.sh` now injects `game_state.json` into every session prompt (15 entities capped, graceful no-op if absent)
- `prompts/system.md`: added `## READING GAME STATE` section teaching Claude to use entity coordinates for targeting
- Skills: `/game-session`, `/verify-pipeline`, `/training-summary` in `.claude/commands/`
- `CLAUDE.md`: added GOTCHAS, SESSION STARTUP ORDER, CURRENT STATUS, SESSION STARTUP sections at top

**Verified live:**
- Prompt injection confirmed — `nearby_entities` visible in process args (`ps aux`)
- ClaudeBot logged in, teleported to Mudwich, entered active combat with Rat (HP 12/20 screenshot), +5 XP captured in `game_state.json`
- ws_observer maintained 40+ entities throughout

**Key decisions:**
- Skills go in `.claude/commands/` (not `.claude/skills/`) — that's where Claude Code slash commands are loaded from
- play.sh subprocess deadlock: running `claude -p` as a child of Claude Code deadlocks both on the shared Playwright MCP browser. Must always use a separate terminal.
- ws_observer spawn offset: observer stays at tile 328,892 (Programmer's house), ClaudeBot is at 188,157 (Mudwich). `nearby_entities` reflects observer's area, not Claude's. Known limitation.

**Next:**
- Merge PR3 (`feat/pr3-text-state`) and PR4 (skills/CLAUDE.md)
- Consider making ws_observer track ClaudeBot's tile position
