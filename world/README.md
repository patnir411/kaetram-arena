# Kaetram World Model

A lean Transformer forward dynamics model that predicts combat outcomes in Kaetram. Given a game state + action, it predicts HP changes, XP gains, entity interactions, and death risk.

## What It Does

```
Input:  (current_state, action) → e.g. "50 HP, Rat nearby, attack"
Output: (delta_state, reward, death_prob) → "lose 3 HP, gain 10 XP, 0.1% death risk"
```

The MCTS planner chains these predictions forward to evaluate multi-step strategies before the agent acts.

## Architecture

- **State**: 16-dim combat-focused vector (HP, level, XP, top-3 entity stats, death/poison/target flags)
- **Action**: 26-dim one-hot + continuous args
- **Model**: 4-layer Transformer (~2.2M params), predicts state deltas + reward + terminal
- **Training**: 4 seconds on Modal T4 GPU | Val loss: 0.062

## Quickstart

```bash
# 1. Extract transitions from agent session logs
.venv/bin/python3 -m world.extract_transitions --log-dir dataset/raw/ --output dataset/world_model/transitions.jsonl

# 2. Train on Modal (T4 GPU, ~$0.01)
modal run world/train_modal.py

# 3. Download checkpoint
modal volume get kaetram-world-vol best.pt world/checkpoints/best.pt --force

# 4. Run interactive demo
.venv/bin/python3 -m world.demo

# 5. Evaluate accuracy
.venv/bin/python3 -m world.evaluate --model world/checkpoints/best.pt --data dataset/world_model/transitions.jsonl --verbose

# 6. Run MCTS planner
.venv/bin/python3 -m world.mcts --model world/checkpoints/best.pt --simulations 200
```

## State Vector (16 dims)

| Index | Field | Purpose |
|---|---|---|
| 0 | hp_ratio | Current HP / max HP |
| 1 | max_hp | Max HP / 1000 |
| 2 | level | Level / 100 |
| 3 | xp | Experience / 100k |
| 4-6 | ent0 (hp, dist, aggro) | Nearest entity |
| 7-9 | ent1 (hp, dist, aggro) | 2nd nearest entity |
| 10-12 | ent2 (hp, dist, aggro) | 3rd nearest entity |
| 13 | is_dead | Terminal flag |
| 14 | is_poisoned | DOT damage |
| 15 | has_target | In combat |

## Files

| File | Purpose |
|---|---|
| `schema.py` | State (16-dim) and action (26-dim) encoding |
| `model.py` | Transformer forward dynamics model |
| `extract_transitions.py` | Raw JSONL logs → (state, action, next_state) triads |
| `train.py` | Local PyTorch training |
| `train_modal.py` | Modal cloud training (T4 GPU) |
| `evaluate.py` | Per-field accuracy + rollout drift metrics |
| `mcts.py` | MCTS planner for look-ahead planning |
| `demo.py` | Interactive terminal demo |

## Practical Use Cases

1. **Death prevention guardrail** — simulate proposed actions, block if death risk > threshold
2. **Prompt enrichment** — show the LLM predicted outcomes per action before it decides
3. **GRPO reward shaping** — use predicted XP/HP as dense reward signals for RL training
