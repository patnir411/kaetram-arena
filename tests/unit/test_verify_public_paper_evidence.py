from pathlib import Path

from scripts.verify_public_paper_evidence import verify_public_evidence


REPO = Path(__file__).resolve().parents[2]


def test_checked_in_paper_evidence_reproduces():
    result = verify_public_evidence(REPO)

    assert result["july_score_replay"]["observation_count"] == 21_524
    assert result["trigger_incidence"]["scheduled_requests"] == 1_200
    assert result["trigger_incidence"]["successful_requests"] == 1_200
    assert result["trigger_incidence"]["failed_requests"] == 0
    assert result["trigger_incidence"]["independent_cell_count"] == 12
    assert result["trigger_incidence"]["independent_contrast_count"] == 9
    assert result["trigger_incidence"]["state_condition_groups"] == 240
    assert (
        result["trigger_incidence"]["groups_with_identical_semantic_responses"]
        == 240
    )
