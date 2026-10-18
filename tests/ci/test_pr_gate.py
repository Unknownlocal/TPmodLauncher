# SPDX-License-Identifier: MPL-2.0
"""Exercise manual CI claims, races and status publication without GitHub access."""

import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ci_pr", ROOT / ".github/ci_pr.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
SHA = "a" * 40
WORKFLOW_SHA = "b" * 40
TAG_SHA = "c" * 40


def http_error(code):
    return urllib.error.HTTPError("https://api.github.com/test", code, "test", {}, io.BytesIO())


class FakeGitHub:
    repository = "owner/repo"

    def __init__(self):
        self.calls = []
        self.pr = {"state": "open", "base": {"ref": "main"},
                   "head": {"sha": SHA, "repo": {"full_name": "contributor/fork"}}}
        self.tags = {}
        self.ref = None
        self.race = False
        self.status_failure = False

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if method == "GET" and path == "/pulls/7":
            return self.pr
        if method == "GET" and path.startswith("/git/ref/tags/"):
            if self.ref is None:
                raise http_error(404)
            return self.ref
        if method == "GET" and path.startswith("/git/tags/"):
            return self.tags[path.rsplit("/", 1)[1]]
        if method == "POST" and path == "/git/tags":
            self.tags[TAG_SHA] = {"sha": TAG_SHA, "message": data["message"],
                                  "object": {"sha": data["object"], "type": data["type"]}}
            return {"sha": TAG_SHA}
        if method == "POST" and path == "/git/refs":
            self.ref = {"object": {"type": "tag", "sha": data["sha"]}}
            if self.race:
                record = json.loads(self.tags[TAG_SHA]["message"])
                record["run_id"] = 99
                self.tags[TAG_SHA]["message"] = json.dumps(record)
                raise http_error(422)
            return self.ref
        if method == "POST" and path.startswith("/statuses/"):
            if self.status_failure:
                raise http_error(503)
            return data
        if method == "POST" and path == "/check-runs":
            return data
        raise AssertionError(f"Unexpected request: {method} {path}")


def states(api):
    return [data["state"] for method, path, data in api.calls if path.startswith("/statuses/")]


def test_instructions_link_fork_pr_head_to_workflow_without_claiming_or_passing_ci():
    api = FakeGitHub()
    summary = ci.instructions(api, "7")
    check = next(data for _, path, data in api.calls if path == "/check-runs")
    assert check["head_sha"] == SHA
    assert check["details_url"] == "https://github.com/owner/repo/actions/workflows/tests.yml"
    assert check["conclusion"] == "neutral"
    assert check["name"] != ci.CONTEXT
    assert "**main**" in summary and "**7** as the PR number" in summary
    assert check["output"]["summary"] == summary
    assert states(api) == []
    assert api.ref is None


def test_instructions_cannot_replace_a_claimed_commits_result():
    api = FakeGitHub()
    ci.claim(api, 7, 42, WORKFLOW_SHA)
    ci.publish(api, SHA, 42, "success")
    original = len(api.calls)
    ci.instructions(api, 7)
    assert states(api) == ["pending", "success"]
    assert [path for method, path, _ in api.calls[original:] if method == "POST"] == ["/check-runs"]
    assert ci.marker(api, SHA)["run_id"] == 42


@pytest.mark.parametrize("state,base", [("closed", "main"), ("open", "other")])
def test_instructions_reject_ineligible_prs_without_writing_checks(state, base):
    api = FakeGitHub()
    api.pr.update(state=state, base={"ref": base})
    with pytest.raises(ValueError, match="open pull request targeting main"):
        ci.instructions(api, 7)
    assert not any(method == "POST" for method, _, _ in api.calls)


def test_fork_pr_claims_exact_head_and_links_pending_status_to_original_run():
    api = FakeGitHub()
    record = ci.claim(api, "7", "42", WORKFLOW_SHA)
    assert record == {"sha": SHA, "pr": 7, "run_id": 42, "workflow_sha": WORKFLOW_SHA}
    assert ci.marker(api, SHA) == record
    assert states(api) == ["pending"]
    status = next(data for _, path, data in api.calls if path.startswith("/statuses/"))
    assert status["context"] == "manual-ci"
    assert status["target_url"] == "https://github.com/owner/repo/actions/runs/42"
    assert next(data for _, path, data in api.calls if path == "/git/refs")["ref"] == "refs/tags/ci-tested/" + SHA


def test_second_dispatch_refuses_existing_claim_without_overwriting_result():
    api = FakeGitHub()
    ci.claim(api, 7, 42, WORKFLOW_SHA)
    ci.publish(api, SHA, 42, "success")
    original = len(api.calls)
    with pytest.raises(ValueError, match="already claimed.*actions/runs/42"):
        ci.claim(api, 7, 43, WORKFLOW_SHA)
    assert not any(method == "POST" for method, _, _ in api.calls[original:])
    assert states(api) == ["pending", "success"]


def test_racing_claims_allow_only_the_first_ref_creation_to_publish_pending():
    api = FakeGitHub()
    api.race = True
    with pytest.raises(ValueError, match="already claimed.*actions/runs/99"):
        ci.claim(api, 7, 42, WORKFLOW_SHA)
    assert states(api) == []


def test_failed_status_write_keeps_claim_and_prevents_retry():
    api = FakeGitHub()
    api.status_failure = True
    with pytest.raises(urllib.error.HTTPError):
        ci.claim(api, 7, 42, WORKFLOW_SHA)
    api.status_failure = False
    with pytest.raises(ValueError, match="already claimed"):
        ci.claim(api, 7, 43, WORKFLOW_SHA)


@pytest.mark.parametrize("result,expected", [("success", "success"), ("failure", "failure"),
                                           ("skipped", "failure"), ("cancelled", "failure")])
def test_only_complete_success_publishes_success(result, expected):
    api = FakeGitHub()
    ci.claim(api, 7, 42, WORKFLOW_SHA)
    assert ci.publish(api, SHA, 42, result) == expected
    assert states(api) == ["pending", expected]


def test_another_run_cannot_publish_a_success_for_the_claimed_commit():
    api = FakeGitHub()
    ci.claim(api, 7, 42, WORKFLOW_SHA)
    with pytest.raises(ValueError, match="Only the run that claimed"):
        ci.publish(api, SHA, 43, "success")
    assert states(api) == ["pending"]


@pytest.mark.parametrize("state,base,repo", [("closed", "main", {}), ("open", "other", {}),
                                           ("open", "main", None)])
def test_unavailable_or_wrong_target_pr_is_rejected_before_claim(state, base, repo):
    api = FakeGitHub()
    api.pr.update(state=state, base={"ref": base}, head={"sha": SHA, "repo": repo})
    with pytest.raises(ValueError):
        ci.claim(api, 7, 42, WORKFLOW_SHA)
    assert not any(method == "POST" for method, _, _ in api.calls)


@pytest.mark.parametrize("value", ["1\nsha=bad", "0", "-7", "7; echo bad", "7.0", " 7", "07"])
def test_pr_input_cannot_inject_output_or_change_api_paths(value):
    api = FakeGitHub()
    with pytest.raises(ValueError):
        ci.claim(api, value, 42, WORKFLOW_SHA)
    assert api.calls == []


@pytest.mark.parametrize("ref,attempt", [("refs/heads/main", "2"), ("refs/heads/feature", "1"),
                                        ("refs/tags/main", "1")])
def test_reruns_and_other_workflow_refs_are_refused(ref, attempt):
    with pytest.raises(ValueError):
        ci.require_first_attempt(ref, attempt)


def test_corrupt_marker_fails_closed():
    api = FakeGitHub()
    ci.claim(api, 7, 42, WORKFLOW_SHA)
    api.tags[TAG_SHA]["object"]["sha"] = WORKFLOW_SHA
    with pytest.raises(ValueError, match="different commit"):
        ci.publish(api, SHA, 42, "success")
    assert states(api) == ["pending"]


def test_claim_cli_writes_only_validated_sha_and_pr(monkeypatch, tmp_path):
    api = FakeGitHub()
    monkeypatch.setattr(ci, "GitHub", lambda *args: api)
    for key, value in {"GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
                       "GITHUB_REPOSITORY": api.repository, "GH_TOKEN": "not-a-real-token",
                       "GITHUB_RUN_ID": "42", "GITHUB_SHA": WORKFLOW_SHA, "PR_NUMBER": "7",
                       "GITHUB_OUTPUT": str(tmp_path / "output"),
                       "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}.items():
        monkeypatch.setenv(key, value)
    assert ci.main(["claim"]) == 0
    assert (tmp_path / "output").read_text() == f"sha={SHA}\npr=7\n"
    assert SHA in (tmp_path / "summary").read_text()
