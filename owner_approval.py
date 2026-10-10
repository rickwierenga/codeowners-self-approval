"""Check path ownership using trusted main, author consent, and current reviews."""

import json
from functools import cache
import os
from pathlib import Path
import re
import subprocess

REPO = os.environ["GITHUB_REPOSITORY"]
ROOT = f"repos/{REPO}"


def api(endpoint, body=None, *, method="POST", paginate=False, key=None, accept="application/vnd.github+json"):
    token = os.environ["APP_TOKEN"]
    command = ["gh", "api", "--hostname", "github.com", "-H", f"Accept: {accept}", endpoint]
    if paginate:
        command += ["--paginate", "--slurp"]
    if body is not None:
        command += ["--method", method, "--input", "-"]
    result = subprocess.run(
        command, input=json.dumps(body) if body is not None else None,
        text=True, capture_output=True, check=True,
        env={**os.environ, "GH_TOKEN": token},
    )
    value = json.loads(result.stdout)
    return [item for page in value for item in (page[key] if key else page)] if paginate else value


def git(*args):
    return subprocess.check_output(["git", *args])


def owners_for(policy):
    rules = []
    for line in policy.splitlines():
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        pattern, *owners = fields
        # Deliberately small CODEOWNERS subset; reject unknown syntax.
        if not owners or any(not re.fullmatch(r"@[\w-]+(?:/[\w-]+)?", o) for o in owners):
            raise ValueError(f"Invalid owners: {line}")
        if pattern == "*":
            regex = re.compile(r"(?s:.*)")
        else:
            if not re.fullmatch(r"/[\w./*?-]+", pattern) or "**" in pattern:
                raise ValueError(f"Unsupported pattern: {pattern}")
            parts = pattern.strip("/").split("/")
            if any(p in ("", ".", "..") for p in parts):
                raise ValueError(f"Invalid path: {pattern}")
            if any("*" in p or "?" in p for p in parts[:-1]):
                raise ValueError(f"Wildcard directories are unsupported: {pattern}")
            expression = re.escape(pattern[1:]).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
            suffix = ".*" if pattern.endswith("/") else ("" if "*" in pattern or "?" in pattern else "(?:/.*)?")
            regex = re.compile(expression + suffix, re.DOTALL)
        rules.append((regex, {o.lower() for o in owners}))

    def lookup(path):
        return next((owners for regex, owners in reversed(rules) if regex.fullmatch(path)), set())

    return lookup


@cache
def team_members(owner):
    org, slug = owner[1:].split("/")
    if org.lower() != REPO.split("/")[0].lower():
        return set()
    endpoint = f"orgs/{org}/teams/{slug}"
    team = api(endpoint)
    access = api(f"{endpoint}/repos/{REPO}", accept="application/vnd.github.v3.repository+json")
    if team["privacy"] == "secret" or not access["permissions"].get("push"):
        return set()
    return {u["login"].lower() for u in api(f"{endpoint}/members?per_page=100", paginate=True)}


@cache
def writer(login):
    return api(f"{ROOT}/collaborators/{login}/permission")["permission"] in {"write", "maintain", "admin"}


def review_decisions(reviews, head):
    # A comment does not supersede a review. A dismissal must not revive an old approval.
    latest = {}
    for review in sorted(reviews, key=lambda r: r["id"]):
        if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[review["user"]["login"].lower()] = review
    approved, blocking = set(), set()
    for login, review in latest.items():
        if not writer(login):
            continue
        if review["state"] == "CHANGES_REQUESTED":
            blocking.add(login)
        elif review["state"] == "APPROVED" and review["commit_id"] == head:
            approved.add(login)
    return approved, blocking


def uncovered(paths, lookup, people):
    def covered(path):
        return any(owner[1:] in people if "/" not in owner else bool(team_members(owner) & people)
                   for owner in lookup(path))
    return [path for path in paths if not covered(path)]


def evaluate(number, event):
    endpoint = f"{ROOT}/pulls/{number}"
    pr = api(endpoint)
    if pr["state"] != "open" or pr["base"]["ref"] != "main":
        return
    head, base = pr["head"]["sha"], pr["base"]["sha"]
    author = pr["user"]["login"].lower()
    proof = f"owner:{number}:{pr['user']['id']}:{head}"
    checks = api(f"{ROOT}/commits/{head}/check-runs?check_name=Code%20ownership&filter=all&per_page=100",
                 paginate=True, key="check_runs")
    consent = any(c["app"]["id"] == int(os.environ["APP_ID"]) and c.get("external_id") == proof
                  for c in checks)
    consent |= (os.environ["GITHUB_EVENT_NAME"] == "pull_request_target"
                and event.get("number") == number
                and event["sender"]["id"] == pr["user"]["id"]
                and event["pull_request"]["head"]["sha"] == head)
    check = api(f"{ROOT}/check-runs", {
        "name": "Code ownership", "head_sha": head, "status": "in_progress",
        "external_id": proof if consent else "",
    })
    check_endpoint = f"{ROOT}/check-runs/{check['id']}"
    try:
        if check["app"]["id"] != int(os.environ["APP_ID"]):
            raise ValueError("Token is not from the configured ownership App")
        ref = f"refs/owner-approval/{number}"
        git("fetch", "--no-tags", "--no-recurse-submodules", "origin",
            "refs/heads/main", f"+refs/pull/{number}/head:{ref}")
        if git("rev-parse", ref).decode().strip() != head:
            raise ValueError("PR changed during evaluation; its new event will recheck it")
        lookup = owners_for(git("show", f"{base}:.github/CODEOWNERS").decode())
        paths = git("diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                    "--name-only", "-z", f"{base}...{head}", "--").decode().split("\0")[:-1]
        reviews = api(f"{endpoint}/reviews?per_page=100", paginate=True)
        people, blocking = review_decisions(reviews, head)
        if consent and writer(author):
            people.add(author)
        missing = uncovered(paths, lookup, people)
        current = api(endpoint)
        if ((current["head"]["sha"], current["base"]["sha"], current["base"]["ref"], current["state"])
                != (head, base, "main", "open")
                or reviews != api(f"{endpoint}/reviews?per_page=100", paginate=True)):
            raise ValueError("PR or reviews changed during evaluation; awaiting recheck")
        passed = not missing and not blocking and not current["draft"]
        summary = ("Every changed path is authorized." if passed else
                   f"Unapproved paths: {len(missing)}. Blocking reviews: {len(blocking)}. "
                   f"Draft: {current['draft']}.")
        api(check_endpoint, {"status": "completed", "conclusion": "success" if passed else "failure",
                            "output": {"title": "Code ownership", "summary": summary}}, method="PATCH")
        print(f"PR #{number}: {summary}")
    except Exception:
        api(check_endpoint, {"status": "completed", "conclusion": "failure",
                            "output": {"title": "Ownership could not be verified",
                                       "summary": "See the trusted workflow run for details."}}, method="PATCH")
        raise


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    kind = os.environ["GITHUB_EVENT_NAME"]
    if event["repository"]["full_name"] != REPO or kind not in {"push", "pull_request_target", "workflow_run", "workflow_dispatch"}:
        raise ValueError("Unexpected repository/event")
    if kind == "push" and event["ref"] != "refs/heads/main":
        raise ValueError("Only main may trigger a sweep")
    if kind == "workflow_run" and event["workflow_run"]["event"] != "pull_request_review":
        return
    # Sweep also on review events: fork workflow_run payloads can omit PR numbers.
    numbers = [p["number"] for p in api(f"{ROOT}/pulls?state=open&base=main&per_page=100", paginate=True)]
    failures = []
    for number in numbers:
        try:
            evaluate(number, event)
        except (subprocess.CalledProcessError, ValueError, RuntimeError) as error:
            failures.append(number)
            print(f"PR #{number}: {error}")
    if failures:
        raise RuntimeError(f"Could not evaluate PRs: {failures}")


if __name__ == "__main__":
    main()
