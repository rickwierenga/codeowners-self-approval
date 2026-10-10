"""Approve a PR only when its author owns every changed path. Run from trusted main."""

import json
from functools import cache
import os
from pathlib import Path
import re
import subprocess

REPO = os.environ["GITHUB_REPOSITORY"]
ROOT = f"repos/{REPO}"


def api(endpoint, body=None, *, approval=False, paginate=False, accept="application/vnd.github+json"):
    token = os.environ["APPROVAL_TOKEN" if approval else "READ_TOKEN"]
    command = ["gh", "api", "--hostname", "github.com", "-H", f"Accept: {accept}", endpoint]
    if paginate:
        command += ["--paginate", "--slurp"]
    if body is not None:
        command += ["--method", "POST", "--input", "-"]
    result = subprocess.run(
        command, input=json.dumps(body) if body is not None else None,
        text=True, capture_output=True, check=True,
        env={**os.environ, "GH_TOKEN": token},
    )
    value = json.loads(result.stdout)
    return [item for page in value for item in page] if paginate else value


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
    team = api(endpoint, approval=True)
    access = api(f"{endpoint}/repos/{REPO}", approval=True, accept="application/vnd.github.v3.repository+json")
    if team["privacy"] == "secret" or not access["permissions"].get("push"):
        return set()
    return {u["login"].lower() for u in api(f"{endpoint}/members?per_page=100", approval=True, paginate=True)}


def approve(number, event, bot):
    endpoint = f"{ROOT}/pulls/{number}"
    pr = api(endpoint)
    if pr["state"] != "open" or pr["draft"] or pr["base"]["ref"] != "main":
        return
    head, base = pr["head"]["sha"], pr["base"]["sha"]
    reviews = api(f"{endpoint}/reviews?per_page=100", paginate=True)
    proof = f"Owner authorization: {pr['user']['id']}:{head}:"
    prior = [r for r in reviews if r["user"]["id"] == bot["id"]
             and r["commit_id"] == head and (r.get("body") or "").startswith(proof)
             and r["state"] in {"APPROVED", "DISMISSED"}]
    author_event = (event.get("number") == number and event["sender"]["id"] == pr["user"]["id"]
                    and event["pull_request"]["head"]["sha"] == head)
    # A main push can renew authorization of the SAME revision, never a stranger's new push.
    if not author_event and not prior:
        return
    author = pr["user"]["login"]
    permission = api(f"{ROOT}/collaborators/{author}/permission")["permission"]
    if permission not in {"write", "maintain", "admin"}:
        return
    if pr["user"]["id"] == bot["id"]:
        return

    # Fetch objects only: never checkout or execute anything from the PR.
    ref = f"refs/owner-approval/{number}"
    git("fetch", "--no-tags", "--no-recurse-submodules", "origin",
        "refs/heads/main", f"+refs/pull/{number}/head:{ref}")
    if git("rev-parse", ref).decode().strip() != head:
        return False
    lookup = owners_for(git("show", f"{base}:.github/CODEOWNERS").decode())
    # No API pagination/truncation. Disabling renames checks old AND new paths.
    paths = git("diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                "--name-only", "-z", f"{base}...{head}", "--").decode().split("\0")[:-1]
    def authorized(path):
        owners = lookup(path)
        return (not path.startswith(".github/") and f"@{bot['login'].lower()}" in owners
                and (f"@{author.lower()}" in owners or any(
                    "/" in owner and author.lower() in team_members(owner) for owner in owners)))

    if not paths or not all(authorized(path) for path in paths):
        print(f"PR #{number}: ordinary CODEOWNERS reviews apply.")
        return
    current = api(endpoint)
    if (current["head"]["sha"], current["base"]["sha"], current["base"]["ref"], current["state"]) != (
        head, base, "main", "open"
    ):
        return False # Retry with the new base/head snapshot.
    if any(r["state"] == "APPROVED" and r["body"] == proof + base for r in prior):
        return
    api(f"{endpoint}/reviews", {"event": "APPROVE", "commit_id": head, "body": proof + base}, approval=True)
    print(f"Approved PR #{number} at {head}.")


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    kind = os.environ["GITHUB_EVENT_NAME"]
    if event["repository"]["full_name"] != REPO or kind not in {"push", "pull_request_target"}:
        raise ValueError("Unexpected repository/event")
    if kind == "push" and event["ref"] != "refs/heads/main":
        raise ValueError("Only main may trigger a sweep")
    numbers = ([int(event["number"])] if kind == "pull_request_target" else
               [p["number"] for p in api(f"{ROOT}/pulls?state=open&base=main&per_page=100", paginate=True)])
    bot = api("user", approval=True)
    failures = []
    for number in numbers:
        try:
            for _ in range(3):
                if approve(number, event, bot) is not False:
                    break
            else:
                raise RuntimeError("PR kept changing during evaluation")
        except (subprocess.CalledProcessError, ValueError, RuntimeError) as error:
            failures.append(number)
            print(f"PR #{number}: {error}")
    if failures:
        raise RuntimeError(f"Could not evaluate PRs: {failures}")


if __name__ == "__main__":
    main()
