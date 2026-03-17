---
description: Trigger when the user asks about training data, dataset stats, reward trends, how many steps have been recorded, "how is training going", "show me the dataset", "is the reward going up", or wants a summary of collected experience.
---

Analyze the full training dataset and print a summary report.

**Step 1 — detect project root:**
```bash
PROJECT_DIR=$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || pwd)
```

**Step 2 — run the full analysis:**
```python
import json, os, glob, datetime

dataset_dir = f"{PROJECT_DIR}/dataset"
history_path = f"{PROJECT_DIR}/.claude/commands/training-summary/history.json"

session_dirs = sorted(glob.glob(f"{dataset_dir}/*/steps.jsonl"))
if not session_dirs:
    print("No training data found in dataset/")
    exit()

all_sessions = []
for path in session_dirs:
    session_name = os.path.basename(os.path.dirname(path))
    lines = [l.strip() for l in open(path) if l.strip()]
    steps = []
    for l in lines:
        try: steps.append(json.loads(l))
        except: pass
    if not steps:
        continue
    rewards = [s.get("reward", 0) for s in steps]
    entity_steps = sum(1 for s in steps if s.get("state", {}).get("nearby_entities"))
    all_sessions.append({
        "session": session_name,
        "steps": len(steps),
        "avg_reward": round(sum(rewards) / len(rewards), 3) if rewards else 0,
        "total_reward": round(sum(rewards), 3),
        "best_reward": max(rewards) if rewards else 0,
        "entity_steps": entity_steps,
        "screenshot_only_steps": len(steps) - entity_steps,
        "first_timestamp": steps[0].get("timestamp"),
        "last_timestamp": steps[-1].get("timestamp"),
    })

total_steps = sum(s["steps"] for s in all_sessions)
total_entity_steps = sum(s["entity_steps"] for s in all_sessions)
best_session = max(all_sessions, key=lambda s: s["avg_reward"])
worst_session = min(all_sessions, key=lambda s: s["avg_reward"])

print(f"=== Training Dataset Summary ===")
print(f"Sessions:     {len(all_sessions)}")
print(f"Total steps:  {total_steps}")
print(f"Entity steps: {total_entity_steps} ({100*total_entity_steps//total_steps if total_steps else 0}% have entity data)")
print(f"Screenshot-only: {total_steps - total_entity_steps}")
print()
print(f"{'Session':<20} {'Steps':>6} {'AvgReward':>10} {'TotalReward':>12} {'EntitySteps':>12}")
print("-" * 65)
for s in all_sessions:
    print(f"{s['session']:<20} {s['steps']:>6} {s['avg_reward']:>10.3f} {s['total_reward']:>12.3f} {s['entity_steps']:>12}")
print()
print(f"Best session:  {best_session['session']} (avg reward {best_session['avg_reward']})")
print(f"Worst session: {worst_session['session']} (avg reward {worst_session['avg_reward']})")

# Reward trend
if len(all_sessions) >= 2:
    first_half = all_sessions[:len(all_sessions)//2]
    second_half = all_sessions[len(all_sessions)//2:]
    first_avg = sum(s["avg_reward"] for s in first_half) / len(first_half)
    second_avg = sum(s["avg_reward"] for s in second_half) / len(second_half)
    trend = "UP ↑" if second_avg > first_avg else "DOWN ↓" if second_avg < first_avg else "FLAT →"
    print(f"\nReward trend:  {trend} (first half avg {first_avg:.3f} → second half avg {second_avg:.3f})")
else:
    print(f"\nReward trend:  insufficient sessions for trend (need 2+)")

# Save history
os.makedirs(os.path.dirname(history_path), exist_ok=True)
history = []
if os.path.exists(history_path):
    try: history = json.load(open(history_path))
    except: pass
history.append({
    "run_at": datetime.datetime.now().isoformat(),
    "sessions": len(all_sessions),
    "total_steps": total_steps,
    "entity_step_pct": 100*total_entity_steps//total_steps if total_steps else 0,
    "avg_reward_overall": round(sum(s["avg_reward"] for s in all_sessions)/len(all_sessions), 3),
    "best_session": best_session["session"],
    "per_session": all_sessions,
})
json.dump(history, open(history_path, "w"), indent=2)
print(f"\nSummary saved to .claude/commands/training-summary/history.json")
```
