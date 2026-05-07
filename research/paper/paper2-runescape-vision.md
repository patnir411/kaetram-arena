# Paper 2: RuneScape Adversarial Multi-Agent — Vision & Research Agenda

_Sourced from Linear KAE-26, KAE-31. This is a planning document, not paper-ready prose._

---

## One-Sentence Framing

Train AI agents to execute and defend against adversarial social strategies in a virtual economy — grounded in the RuneScape scam taxonomy as a 20-year corpus of human-generated adversarial behavior.

---

## Why This Paper Exists

Paper 1 (Kaetram) proves the distillation infrastructure works. Paper 2 is where the **agent safety research** lives. No existing paper studies adversarial multi-agent dynamics using the RuneScape scam taxonomy as ground truth. This directly maps to AI safety concerns that Anthropic, DeepMind, and the alignment community care about:

- Deception detection in multi-agent systems
- Social manipulation via economic incentives
- Trust modeling and reputation emergence
- Adversarial curricula grounded in real human behavior data

---

## Platform Decision

**LostCityRS + rs-sdk.** Do NOT use Kaetram — these are separate projects.

| Component | Repo | Why |
|-----------|------|-----|
| Game server | `github.com/LostCityRS/Server` | MIT license, TypeScript/Node.js (same stack as Kaetram), active development, real player base at `2004.lostcity.rs` |
| AI harness | `github.com/MaxBittker/rs-sdk` | 534 stars, MIT, WebSocket gateway, agent bindings, multi-agent architecture, leaderboard |
| Markets | `markets.lostcity.rs` | Real player-to-player trading infrastructure (Feb 2026) — Grand Exchange foundation |
| ElizaOS fork | `github.com/elizaOS/eliza-2004scape` | Existing research community already working on this platform |

---

## Research Tracks

### Track 1: Adversarial Curriculum via Scam Taxonomy

Use the documented RuneScape scam taxonomy (oldschool.runescape.wiki/w/Scams) as ground truth for designing adversarial training scenarios:

- **Trust exploitation** — doubling money pattern (agent promises to double GP, takes it)
- **Information manipulation** — false price signals in GE (pump-and-dump, margin manipulation)
- **Coordinated luring** — multi-agent decoy + attacker (one agent distracts, another attacks)
- **UI/interface exploitation analogs** — trade window manipulation, item swap mid-trade

### Track 2: Virtual Economy Emergence

- Deploy agents with economic goals (maximize GP) in Grand Exchange-like marketplace
- Do agents **independently discover** market manipulation strategies?
- Does reputation/memory **prevent** exploitation?
- Connects to mechanism design literature

### Track 3: Social Deception Detection

- Train detector agents to identify scam patterns from behavioral signals alone
- RuneScape history provides 20-year ground truth labels (6.9M accounts banned in 2024)
- Jagex's "Botwatch" (2012) is an adversarial detection system — directly analogous
- **Directly applicable to AI safety:** detecting deceptive agents in multi-agent systems

### Track 4: Memory, Trust & Reputation

- Agents maintain persistent memory of interactions across sessions
- Do reputation systems **emerge** without being explicitly designed?
- What memory architecture best supports long-horizon trust modeling?

---

## Critical Prior Work

| Paper | Venue | Relevance |
|-------|-------|-----------|
| DeepMind "Virtual Agent Economies" | arXiv 2509.10147, Sept 2025 | Theoretical framing paper — MUST cite. We provide the empirical testbed they describe. |
| "The Traitors" | arXiv 2505.12923 | Closest to trust/reputation axis |
| "Secret Collusion among AI Agents" | NeurIPS 2024 | Formalizes multi-agent deception |
| Anthropic "Agentic Misalignment" | arXiv 2510.05179 | Directly adjacent — shows LLM agents exhibit insider-threat behaviors. We provide controlled testbed. |

---

## Window Warning

The rs-sdk/runescape-bench ecosystem is growing fast (534 stars, HN coverage, ElizaOS integration). DeepMind already published theoretical framing for virtual agent economies. **Paper 2 should be submitted within 12 months of Paper 1 to establish priority.**

---

## Setup TODOs (KAE-31)

- [ ] Clone LostCityRS/Server, run locally, verify startup
- [ ] Clone MaxBittker/rs-sdk, connect to local server, basic agent loop
- [ ] Understand Markets codebase — trading API surface
- [ ] Design Grand Exchange order book spec (buy/sell offers, price matching, item sink)
- [ ] Design first adversarial scenario: 2 agents trade, 1 incentivized to scam, measure detection rate over N episodes
- [ ] Spec MCP server for LostCityRS (reuse Kaetram patterns)
- [ ] Literature review: RuneScape economy papers, virtual economy research, mechanism design
- [ ] ICLR/arXiv framing for adversarial curriculum angle

---

## Connection to Paper 1

The MCP tool API architecture from Paper 1 transfers directly — same pattern of typed tools, Playwright automation, structured data collection. Paper 2 adds:
- Multi-agent interaction (agents interact with each other, not just the environment)
- Adversarial incentives (zero-sum economic games)
- Deception as a measurable outcome
- Trust/reputation as emergent phenomena

**The two papers share infrastructure DNA but are fully independent research contributions.**
