# Literature positioning — verified July 19, 2026

## Candidate contribution boundary

The paper is not novel because it uses games, MCP, structured actions, a persistent world, intermediate-state OPD, or on-policy distillation separately. TCOD already uses teacher-success prefixes to initialize intermediate states for multi-turn OPD; Guided-OPD changes live state occupancy through decaying teacher turns; ReOPD explicitly frames multi-turn OPD as reliability-aware prefix-distribution design; and SCoRe trains from verified prefixes immediately before student errors. The candidate contribution is contingent on the planned matched experiments:

1. **Reachability-targeted persistent-player-state initialization for on-policy distillation:** directly restore witness-certified persistent player states that need not lie on a successful teacher path; select them using a frozen student-visitation, teacher-advantage, recoverability, and task-relevance rule; train on student rollouts from those states; and compare canonical-start evaluation against natural OPD, random-valid and progress-matched resets, TCOD-B2F, and Guided-OPD. The implementation does not restore a complete shared world.
2. **A teacher-forcing copy-prior diagnostic:** one targeted historical probe reportedly gives a malformed continuation positive teacher-over-student advantage, but it does not score both candidates in the same matched context. A multi-defect, multi-state, multi-teacher paired intervention is required before treating this as a mechanism contribution.
3. **An interface-versus-weights decomposition protocol:** the historical 18/30 system combines weights and recovery. Only a fully replicated weights-by-recovery factorial can identify main or interaction effects.

Do not use “to our knowledge” novelty language until the matched comparisons succeed and the literature search is rerun immediately before submission.

## Closest on-policy distillation work

- **GKD / On-Policy Distillation of Language Models** establishes student-generated rollouts with teacher feedback and was published at ICLR 2024. It preempts broad OPD novelty. [ICLR paper](https://proceedings.iclr.cc/paper_files/paper/2024/hash/5be69a584901a26c521c2b51e40a4c20-Abstract-Conference.html)
- **On-Policy Context Distillation** studies context-conditioned teachers, including system-prompt distillation and text-based games. [arXiv:2602.12275](https://arxiv.org/abs/2602.12275)
- **Privileged Information Distillation** uses privileged context to teach multi-turn/action behavior without ordinary cross-vocabulary SFT. [arXiv:2602.04942](https://arxiv.org/abs/2602.04942)
- **Canonical-Context OPD (CCOPD)** conditions a frozen teacher on a clean full presentation while the student acts on its own raw multi-turn history, directly preempting broad clean-context or self-anchored-drift novelty. [arXiv:2605.30251](https://arxiv.org/abs/2605.30251)
- **TCOD** introduces a temporal curriculum for multi-turn autonomous-agent OPD and reports long-horizon KL instability. [arXiv:2604.24005](https://arxiv.org/abs/2604.24005)
- **Guided-OPD** mixes teacher- and student-generated turns within each rollout and anneals teacher intervention to zero, improving over vanilla OPD and TCOD in ALFWorld, ScienceWorld, and WebShop. It is the strongest current test of whether teacher anchoring already solves the occupancy problem. [arXiv:2606.15912](https://arxiv.org/abs/2606.15912)
- **ReOPD** replays offline teacher prefixes and samples student decision points with a reliability-aware schedule. It preempts generic claims about designing a better prefix or state distribution. [arXiv:2607.04763](https://arxiv.org/abs/2607.04763)
- **SCoRe** identifies a student's earliest error and starts short-horizon reinforcement learning from the verified pre-error prefix. It preempts broad student-failure-localization claims. [arXiv:2509.14257](https://arxiv.org/abs/2509.14257)
- **On-policy Expert Corrections** switches from a student prefix to an expert suffix and is a strong partially on-policy imitation baseline. [arXiv:2512.14895](https://arxiv.org/abs/2512.14895)
- **Step-wise OPD for Small Language Model Agents (SOD)** directly studies cascading tool errors, worsening teacher reliability after state drift, and divergence-weighted supervision. The present hypothesis is narrower only if a matched probe shows actively wrong-signed candidate preference. [arXiv:2605.07725](https://arxiv.org/abs/2605.07725)
- **Trust-Region Behavior Blending (TRB)** changes early occupancy through a teacher-near behavior policy inside an annealed student-centered KL region while keeping the OPD loss unchanged. It is a primary occupancy baseline candidate. [arXiv:2605.31159](https://arxiv.org/abs/2605.31159)
- **Unmasking On-Policy Distillation** supplies per-token/per-context diagnostics and shows that the useful teacher context depends on task and capacity. It is the strongest precedent for the probe framing. [arXiv:2605.10889](https://arxiv.org/abs/2605.10889)
- **SERL / Selective Hindsight Distillation** studies which environmental feedback sources and insertion points help multi-turn agents. It belongs in related work but is not a mandatory state-reset baseline. [arXiv:2605.19447](https://arxiv.org/abs/2605.19447)
- **Post-Training Is About States, Not Tokens** formalizes the state-distribution view that directly motivates the visitation argument. [arXiv:2605.22731](https://arxiv.org/abs/2605.22731)
- **KAT** identifies low-KL agreement on degraded prefixes and terminates weak supervision. The present copy-prior result is sharper only if it demonstrates wrong-signed endorsement rather than merely absent signal. [arXiv:2606.09471](https://arxiv.org/abs/2606.09471)
- **Rethinking On-Policy Distillation** argues that the teacher must expose genuinely new capabilities and analyzes compatibility between teacher and student behavior. [arXiv:2604.13016](https://arxiv.org/abs/2604.13016)
- **Self-Distillation Enables Continual Learning**, an ICML 2026 Spotlight, evaluates sequential skill accumulation and retention. It is a reason not to label the current same-task rounds “continual learning.” [OpenReview](https://openreview.net/forum?id=HlWA3V6iKF&noteId=DVexpZo7mv)

## Closest game-agent work

- **Orak**, accepted at ICLR 2026, uses MCP to connect LLMs to twelve games, evaluates agentic modules, and provides a gameplay fine-tuning dataset. It invalidates a “first MCP game-agent training” claim. [arXiv:2506.03610](https://arxiv.org/abs/2506.03610)
- **BALROG**, ICLR 2025, evaluates long-horizon language and vision-language agents across games and highlights knowing-versus-doing gaps. [ICLR paper](https://proceedings.iclr.cc/paper_files/paper/2025/hash/f0b1515be276f6ba82b4f2b25e50bef0-Abstract-Conference.html)
- **AgentTrek**, an ICLR 2025 Spotlight, synthesizes trajectories through guided replay from tutorials. It is a close comparison for procedural plans and trajectory collection. [OpenReview](https://openreview.net/forum?id=EEgYUccwsV)
- **lmgame-Bench**, ICLR 2026, studies modular perception, memory, and reasoning harnesses and reports transfer from single-game RL to unseen games and external planning tasks. [arXiv:2505.15146](https://arxiv.org/abs/2505.15146)
- **CRADLE** operates through screenshots and keyboard/mouse control across commercial games and applications, contrasting with Kaetram's symbolic state and typed actions. [OpenReview](https://openreview.net/forum?id=aIAFDFpNXz)
- **Voyager** establishes persistent open-world Minecraft agents with an automatic curriculum and skill library. It preempts generic open-world, persistent, or autonomous-skill-learning claims. [arXiv:2305.16291](https://arxiv.org/abs/2305.16291)
- **GITM** uses structured actions and LLM-generated plans in open-world Minecraft. [arXiv:2305.17144](https://arxiv.org/abs/2305.17144)
- **Backplay** restores states from a demonstration and anneals starts backward toward the canonical initial state. A direct-snapshot claim therefore needs a matched Backplay/history control, not only random resets. [arXiv:1807.06919](https://arxiv.org/abs/1807.06919)

## Positioning sentence

> Prior work changes rollout horizons, replays successful prefixes, mixes teacher and student turns, or corrects errors on encountered student trajectories. We test a narrower control point: direct restoration of witness-certified persistent player states selected by frozen visitation and teacher-advantage criteria. The confirmatory design matches model-visible history and evaluates every arm from a fresh canonical-start world with a registered gameplay-RNG seed; no superiority result is currently reported.

## Claims to avoid

- “First MCP game agent” or “first typed game-agent distillation.”
- “First state-aware OPD,” “first intermediate-state OPD,” or “first failure-state curriculum.”
- “Unlike prior work, we initialize from intermediate states.”
- “Embodied agent”; the interface is symbolic and does not test perception or low-level control.
- “Continual learning”; there is no sequential-task retention protocol.
- “Autonomous skill learning”; the prompt includes procedural quest knowledge and there is no learned skill library.
- “World-model acquisition”; the world-model code is deprecated and unevaluated.
- “Capacity is not the lever”; the model-size sweep is underpowered and duration-mismatched.
