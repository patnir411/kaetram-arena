"""$0 hindsight-credit diagnostic on the r10 SFT source corpus.

For the 5 Claude source runs (per dataset/qwen_sft/metadata.json), label each
action turn with Monte-Carlo hindsight credit: did ANY quest stage advance
within the next N turns *of the same session*? Cross-tabulate by tool.

Answers: (a) what fraction of the corpus is low-credit / dead-end, and
(b) are high-credit turns disproportionately interact_npc / query_quest.
"""
import sys, bisect
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.log_analysis.parse import (  # noqa: E402
    list_agent_dirs, list_runs, parse_run_sessions,
    progression_for_quests, quest_stage_counts,
)
from scripts.log_analysis.artifact_requirements import (  # noqa: E402
    MissingEvidenceError,
    require_agent_run_logs,
)

SOURCE_RUNS = {
    "run_20260504_140418", "run_20260504_172157", "run_20260504_221206",
    "run_20260505_150033", "run_20260505_214542",
}
try:
    require_agent_run_logs(
        "dataset/raw",
        agents=("agent_0", "agent_1", "agent_2"),
        run_ids=SOURCE_RUNS,
        analysis="r10 hindsight-credit diagnostic",
    )
except MissingEvidenceError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc
ALL_QUESTS = list(quest_stage_counts().keys())
WINDOWS = [5, 10, 20]

tool_total = defaultdict(int)
tool_credit = {N: defaultdict(int) for N in WINDOWS}
tool_in_adv_session = defaultdict(int)
total_action = 0
total_observe = 0
n_sessions = n_adv_sessions = 0
turns_in_adv = turns_in_dead = 0
runs_seen = 0

for ad in list_agent_dirs():
    for rd in list_runs(ad):
        if rd.name not in SOURCE_RUNS:
            continue
        rv = parse_run_sessions(ad, rd)
        if not rv.sessions:
            continue
        runs_seen += 1
        progs = progression_for_quests(rv, quest_names=ALL_QUESTS)
        adv = sorted({ev.tc_run_idx for p in progs.values()
                      for ev in p.stage_events if ev.kind in ("advance", "accept")})
        starts, cum = [], 0
        for sv in rv.sessions:
            starts.append(cum)
            cum += len(sv.tool_calls)
        for si, sv in enumerate(rv.sessions):
            base = starts[si]
            end = base + len(sv.tool_calls)
            local_adv = sorted(a - base for a in adv if base <= a < end)
            has_adv = bool(local_adv)
            n_sessions += 1
            if has_adv:
                n_adv_sessions += 1
                turns_in_adv += len(sv.tool_calls)
            else:
                turns_in_dead += len(sv.tool_calls)
            for j, tc in enumerate(sv.tool_calls):
                if tc.short_name == "observe":
                    total_observe += 1
                    continue
                total_action += 1
                tool_total[tc.short_name] += 1
                if has_adv:
                    tool_in_adv_session[tc.short_name] += 1
                lo = bisect.bisect_right(local_adv, j)
                for N in WINDOWS:
                    if lo < len(local_adv) and local_adv[lo] <= j + N:
                        tool_credit[N][tc.short_name] += 1

print(f"runs parsed:           {runs_seen} (expected 15 = 5 runs x 3 agents)")
print(f"sessions:              {n_sessions}")
print(f"  with >=1 advance:    {n_adv_sessions} ({100*n_adv_sessions/max(n_sessions,1):.0f}%)")
print(f"  dead (no advance):   {n_sessions-n_adv_sessions} ({100*(n_sessions-n_adv_sessions)/max(n_sessions,1):.0f}%)")
print(f"action turns:          {total_action}")
print(f"observe turns:         {total_observe}")
tot = total_action or 1
print(f"  turns in adv sess:   {turns_in_adv}")
print(f"  turns in dead sess:  {turns_in_dead} ({100*turns_in_dead/max(turns_in_adv+turns_in_dead,1):.0f}% of all turns are in dead sessions)")
for N in WINDOWS:
    hc = sum(tool_credit[N].values())
    print(f"action turns with an advance within next {N:>2}: {hc} ({100*hc/tot:.1f}%)")

print("\nPer-tool credit (sorted by count). cred@N = % of that tool's turns "
      "with a quest advance within N turns; sess = % in an advancing session.")
hdr = f"{'tool':<18}{'turns':>7}{'%corpus':>9}{'cred@5':>9}{'cred@10':>9}{'cred@20':>9}{'in_adv_sess':>13}"
print(hdr)
print("-" * len(hdr))
for name in sorted(tool_total, key=lambda k: -tool_total[k]):
    t = tool_total[name]
    row = f"{name:<18}{t:>7}{100*t/tot:>8.1f}%"
    for N in WINDOWS:
        row += f"{100*tool_credit[N][name]/t:>8.0f}%"
    row += f"{100*tool_in_adv_session[name]/t:>12.0f}%"
    print(row)
