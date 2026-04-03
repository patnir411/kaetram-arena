# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-04-03 — Data Audit + Personality Finalization

**Deep audit of all agent logs (189 sessions, 289MB on VM):**
- Confirmed MCP architecture working: 100% semantic tool calls, avg 88 actions/session, 37K thinking chars/session
- Agents at level 70-73, fighting real mid-game content (Bandits, Cow Warriors, Scary Skeletons)
- Attack fix: `post_attack` field added — agent can now confirm damage dealt, eliminates click_tile fallback in combat

**Personalities finalized — dropping to 3:**
- EFFICIENT (agent_3) dropped: 45% click_tile rate, lowest level (37), broken behavior
- METHODICAL prompt rewritten: hard rules (HP < 60% eat first, 2+ food before quest mobs), no more catch-22 prep loop
- Active: AGGRESSIVE (agent_0), METHODICAL (agent_1), CURIOUS (agent_2)
- 3 orthogonal decision axes: combat approach / HP-gated preparation / exploration-first

**Current state:** 3 agents running, training job kicked off on Modal in parallel.

## 2026-04-02 — NPC Interaction Fix + Prompt Rewrite

**Critical bugs fixed:**
- `interact_npc` 95% failure → 100% success (when NPC reachable). Root cause: Chebyshev vs Manhattan adjacency + walking to NPC tile instead of orthogonal neighbor.
- Wife NPC unreachable: wrong door (194,218 = Sorcerer, not Wife). Correct: (310,264).
- Warp cooldown spam: tool now auto-waits internally (up to 25s).
- equip_item: now verifies result, returns equipped true/false with reason.
- MCP "pending" detection: orchestrator auto-restarts stuck sessions.
- Added `drop_item` tool, eat_food HP-full check, login retry loop.

**Prompt rewrite (research-informed):**
- XML tags, calm language (Claude 4.6 over-triggers on aggressive phrasing), WHY clauses on rules.
- Added SEEK QUEST rule: agents actively seek NPCs when no quest is active.
- Removed Methodical food-before-ACCEPT gate, added Efficient NPC-seeking trigger.
- Trimmed game_knowledge ~800 tokens. Total prompt ~2,340 tokens (under 3K threshold).
- Nav snap radius 10→25 (fixes 54% Lakesworld wall failures).

**Results:** Agent 0 completed full Desert Quest (first multi-stage completion). 365+ sessions collected.

## 2026-04-01 — Data Audit + Cleanup Session

- Deleted agent_4 (39 dead Codex sessions), ~260 stub files, pre-March-28 data
- Rebuilt qwen_sft: 1,233 train / 158 val. Created DATA.md.
