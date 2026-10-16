# SPDX-License-Identifier: MPL-2.0
"""Claim one manual CI attempt per PR head commit and publish its result.

Only trusted main-branch jobs execute this helper with a write token. Jobs
executing PR code receive read-only tokens and check out the resolved SHA.
"""

import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request


CONTEXT = "manual-ci"
MARKER_PREFIX = "ci-tested/"


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.repository = repository
        self.token = token

    def request(self, method, path, data=None):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}{path}",
            method=method,
            data=None if data is None else json.dumps(data).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "TPM-manual-CI",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def sha_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected a full commit SHA")
    return value


def positive_number(value):
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ValueError("Expected a positive PR/run number")
    return int(text)


def run_url(api, run_id):
    return f"https://github.com/{api.repository}/actions/runs/{positive_number(run_id)}"


def require_first_attempt(ref, attempt):
    if ref != "refs/heads/main":
        raise ValueError("Run this workflow from main")
    if str(attempt) != "1":
        raise ValueError("Re-runs are disabled: push a new commit before requesting CI again")


def marker(api, sha):
    ref = api.request("GET", f"/git/ref/tags/{MARKER_PREFIX}{sha_value(sha)}")
    if ref["object"]["type"] != "tag":
        raise ValueError("CI marker must be an annotated tag; owner investigation required")
    tag = api.request("GET", f"/git/tags/{sha_value(ref['object']['sha'])}")
    record = json.loads(tag["message"])
    if tag["object"]["sha"] != sha or tag["object"]["type"] != "commit":
        raise ValueError("CI marker points to a different commit")
    if record["sha"] != sha:
        raise ValueError("CI marker metadata does not match the commit")
    positive_number(record["run_id"])
    return record


def reject_existing(api, sha):
    try:
        record = marker(api, sha)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return
        raise
    raise ValueError(f"CI was already claimed for {sha}: {run_url(api, record['run_id'])}")


def claim(api, number, run_id, workflow_sha):
    number, run_id = positive_number(number), positive_number(run_id)
    sha_value(workflow_sha)
    pr = api.request("GET", f"/pulls/{number}")
    if pr["state"] != "open" or pr["base"]["ref"] != "main":
        raise ValueError("CI requires an open pull request targeting main")
    if not pr["head"].get("repo"):
        raise ValueError("The PR source repository is unavailable")
    sha = sha_value(pr["head"]["sha"])
    reject_existing(api, sha)
    record = {"sha": sha, "pr": number, "run_id": run_id, "workflow_sha": workflow_sha}
    # Creating a ref is atomic: competing requests cannot both acquire this SHA.
    tag = api.request("POST", "/git/tags", {
        "tag": MARKER_PREFIX + sha, "message": json.dumps(record, sort_keys=True),
        "object": sha, "type": "commit",
    })
    try:
        api.request("POST", "/git/refs", {"ref": "refs/tags/" + MARKER_PREFIX + sha, "sha": tag["sha"]})
    except urllib.error.HTTPError as error:
        if error.code == 422:
            reject_existing(api, sha)
        raise
    api.request("POST", f"/statuses/{sha}", {
        "state": "pending", "context": CONTEXT, "target_url": run_url(api, run_id),
        "description": "Manual CI claimed; testing this exact PR commit",
    })
    return record


def publish(api, sha, run_id, result):
    sha, run_id = sha_value(sha), positive_number(run_id)
    record = marker(api, sha)
    if record["run_id"] != run_id:
        raise ValueError("Only the run that claimed this commit can publish its CI result")
    if result not in {"success", "failure", "cancelled", "skipped"}:
        raise ValueError("Unexpected aggregate test result")
    state = "success" if result == "success" else "failure"
    api.request("POST", f"/statuses/{sha}", {
        "state": state, "context": CONTEXT, "target_url": run_url(api, run_id),
        "description": "Every required test workflow passed" if state == "success" else "Required test workflows did not all pass",
    })
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("claim", "publish"))
    arguments = parser.parse_args(argv)
    require_first_attempt(os.environ["GITHUB_REF"], os.environ["GITHUB_RUN_ATTEMPT"])
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    run_id = os.environ["GITHUB_RUN_ID"]
    if arguments.operation == "claim":
        record = claim(api, os.environ["PR_NUMBER"], run_id, os.environ["GITHUB_SHA"])
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"sha={record['sha']}\npr={record['pr']}\n")
        summary = (
            f"## Manual CI for PR #{record['pr']}\n\n"
            f"Testing PR head commit `{record['sha']}` exactly once.\n\n"
            "A failed or cancelled attempt requires a new commit; owner bypass remains available.\n"
        )
    else:
        state = publish(api, os.environ["PR_SHA"], run_id, os.environ["CI_RESULT"])
        summary = f"## Required CI result\n\n`{CONTEXT}`: **{state}** for `{os.environ['PR_SHA']}`.\n"
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as output:
        output.write(summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, urllib.error.HTTPError) as error:
        raise SystemExit(str(error))
