# Tool-schema adjudication: live MCP surface vs the two frozen artifacts (2026-07-24)

**Question.** The repo carries two candidate "frozen model-visible tool schema" artifacts:
`scripts/opd/tool_defs.snapshot.json` (maintainer, imported via upstream PR #54; used as the
`OPD_BUILD_TOOLS_JSON` serving-context-parity input for the clean-r1 arm) and
`tool_surface.MODEL_VISIBLE_TOOL_DEFINITIONS` (`kaetram_mcp_v1`, SHA
`770c9a44b1e656c3798577627ddf08928a5787036e22a5e3358bf78ff6432cfe`; the render-contract
freeze from the July-19 audit stack). `research/paper/upstream-pr54-integration.md` excluded
the snapshot on the grounds that its "normalized digest differs from the repository's frozen
model-visible schema; accepting it would reintroduce train/serve prompt drift." This audit
settles the question empirically instead of by assertion.

**Method.** Fresh `list_tools` capture over MCP stdio against `mcp_game_server.py` at this
commit (dev VM, `KAETRAM_DATA_DIR=~/projects/Kaetram-Open/packages/server/data`, no browser
launch — tool listing only), serialized in the same OpenAI `{"type":"function",...}` shape as
both artifacts. Three-way comparison of tool names, parameter properties/types/defaults/enums,
required lists, and descriptions.

## Result

| comparison | structure (names/params/required/types/defaults) | descriptions |
|---|---|---|
| live vs `tool_defs.snapshot.json` | **identical** | **identical** — capture file is **bit-for-bit** the live surface (SHA-256 `53775ffd4501a8ce…` both; canonical-JSON digest `3055da4e1dea27a2…` both) |
| live vs `tool_surface` (`kaetram_mcp_v1`) | **identical** (17/17 tools; no param/required/type/default deltas — the earlier `interact_npc`/`warp`/`combat_style` signature drift was fixed before freeze) | **all 17 differ** — deliberately rewritten/compressed (e.g. `interact_npc` 1505→132 chars, `query_quest` 849→110, `observe` 622→212) |

## Interpretation — there is no drift dispute; there are two lanes

1. **`tool_defs.snapshot.json` is the live serving surface, byte-exact.** Every historical
   rollout, every current `play_qwen` run (default `--tool-schema-source live`), and every
   published checkpoint's serving context carries THESE definitions through the chat
   template. For any build whose gradient context must match what the policy saw at
   generation/serving time — the Seam-1 parity repair validated by clean-r1 (malformed
   emissions 1 vs 233) — this snapshot is the correct, and only correct, `tools=` input.
   The integration-audit's exclusion rationale is inverted for this lane: the snapshot does
   not *introduce* drift; it *is* parity.
2. **`kaetram_mcp_v1` is a deliberate future contract, not a capture.** Its structure matches
   live exactly; its descriptions are a curated compression. In `canonical` mode the client
   sends these definitions, so the model sees a *different* prompt surface than any existing
   checkpoint was trained/evaluated under — which is precisely why KAE-78 requires a fresh
   dataset + retrain under `native_tools_v1` before that mode is used for comparisons. The
   runtime handshake validating functional schema (not descriptions) against live MCP is
   consistent with this design.

## Disposition

- **Both artifacts stay in-tree, lane-labeled.** Snapshot = historical/current-parity input
  (`OPD_BUILD_TOOLS_JSON`); `kaetram_mcp_v1` = the versioned surface for future
  `native_tools_v1` checkpoints.
- **Proposed to Barath:** (a) amend `upstream-pr54-integration.md`'s exclusion rationale to
  the two-lane framing above; (b) either import the live descriptions into a
  `kaetram_mcp_v2` (making the frozen contract a capture, with compression as an explicit
  diff) or keep the compression and document that `canonical` mode is a new interface by
  design; (c) any future MCP tool-surface change requires regenerating the snapshot AND
  bumping the `tool_surface` frozen SHA in the same commit.
- Guard suggestion: a unit test asserting `tool_surface` functional-schema equivalence to
  `tool_defs.snapshot.json` (structure only), so the two artifacts cannot silently diverge
  structurally — descriptions exempt by design.

**Reproduce:** `python3 <capture script over MCP stdio> | sha256sum` vs
`sha256sum scripts/opd/tool_defs.snapshot.json`; structural diff per the comparison script in
the session scratchpad (2026-07-24). Captured on the dev VM at sync-branch HEAD.
