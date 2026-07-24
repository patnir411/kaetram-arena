from __future__ import annotations

import subprocess
import sys


def test_db_helpers_do_not_import_optional_dashboard_server() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from dashboard.db import summarize_quest_doc; "
                "assert 'dashboard.server' not in sys.modules; "
                "quests, summary = summarize_quest_doc(None); "
                "assert quests == []; "
                "assert summary['completed'] == 0"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
