"""pr-tests.yml: يعمل على طلبات الدمج فقط، للقراءة فقط، والـActions مثبتة بالـSHA الكامل، ويشغّل tests وtests_ui."""
import re
from pathlib import Path

WF = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "pr-tests.yml").read_text(encoding="utf-8")


def test_triggers_and_permissions_are_read_only():
    assert re.search(r"^on:\n  pull_request:\n\n", WF, re.M)
    assert re.search(r"^permissions:\n  contents: read\n\n", WF, re.M)
    for banned in ("push:", "schedule:", "workflow_run", "pull_request_target", "workflow_dispatch", "secrets.",
                   "id-token", "pages:", "deploy-pages", "upload-pages-artifact", "git push", "contents: write"):
        assert banned not in WF, banned


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)", WF, re.M)
    assert len(uses) == 4
    assert [u for u in uses if not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u)] == []
    assert WF.count("persist-credentials: false") == WF.count("actions/checkout@")


def test_runs_platform_and_browser_tests_with_the_skip_gate():
    assert "pytest -q -p no:cacheprovider tests --junitxml" in WF
    assert "pytest -q -p no:cacheprovider tests_ui --junitxml" in WF
    assert "pandas==3.1.1 numpy==2.5.4" in WF and "pandas==2.2.0 numpy==1.26.0" in WF
    assert WF.count('for t in ("skipped", "failure", "error")') == 2
