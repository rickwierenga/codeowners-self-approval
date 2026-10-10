# CODEOWNERS self-approval

Let a code owner merge their own PR when **every changed path belongs to them**.
An action supplies the code-owner approval through a dedicated GitHub account.
Other contributors' PRs use normal CODEOWNERS reviews. No server to host.

Supports individual owners and organization teams. Runs on GitHub's Ubuntu runners
against public repositories with a protected `main` branch.

## Installation

1. **Create a dedicated approval account.** Add it to your organization
   and give it Write access to your repository. Keep this account for automation only.
2. **Create its fine-grained PAT.** Select your organization as the resource owner,
   your repository, repository permission **Pull requests: write**, and organization
   permission **Members: read**. Approve the token in the organization if required.
3. **Protect the token.** In your repository's Settings → Environments, create `owner-approval`.
   Select **Selected branches and tags**; add only **Branch: main**. Add no tag
   rules or required reviewers. Store `OWNER_APPROVAL_TOKEN` as an **environment
   secret**, not a repository secret.
4. **Add CODEOWNERS.** Define your paths and owners in `.github/CODEOWNERS`.
   Each line eligible for automatic approval needs its human owners or team
   plus the dedicated approval account.
   Keep `.github/` owned only by trusted human maintainers. Teams must be visible
   and have Write access; the approval account must appear explicitly on each line.
5. **Add the workflow.** Copy [owner-approval.yml](examples/owner-approval.yml)
   into your repository's `.github/workflows/`. It calls:

   ```yaml
   - uses: rickwierenga/codeowners-self-approval@10b90e56a4063725f703452faef9d39a02ffacfc
     with:
       approval-token: ${{ secrets.OWNER_APPROVAL_TOKEN }}
   ```

6. **Protect `main`.** Require pull requests, one approval, code-owner reviews,
   **Dismiss stale pull request approvals when new commits are pushed**, and
   your existing CI checks. Code owners get Write with no bypass. They must
   not be able to administer the protected environment or branch rules.

Install the workflow and CODEOWNERS on `main` through your normal review process.
The action is not a required status check: GitHub's native review requirement is
the merge gate. Update the pinned action commit deliberately when adopting changes.

## Behavior and limits

- PR creation, pushes, reopening, marking ready, and editing trigger evaluation.
  Pushes to `main` recheck open PRs against the current ownership policy.
- The author must currently have Write and own every changed path. A qualifying
  author event or the bot's previous authorization of the exact head commit is
  required. Another person's new commits do not inherit the author's authority.
- A base-change recheck can renew a dismissed bot approval of that same revision.
  This also applies to a manually dismissed bot approval; use a human
  changes-requested review to block merging pending human review.
- PRs spanning different owners require normal human reviews unless the author owns all paths.
  Any writer can merge once the native requirements pass.
- Supported patterns: `*`, anchored files/directories, and `*` or `?` in the final
  component. Last match wins. Unsupported syntax stops automatic approval.
- Removing ownership or membership does not revoke an existing bot approval.
  Token expiration or API failures stop new automatic approvals.
- The action reads trusted base-branch policy, checks both sides of renames,
  and never checks out or executes PR code. Moving snapshots are retried up to
  three times; the final check and approval submission are separate API calls.

Validated with local mocked approval, team, and base-change scenarios. GitHub's
end-to-end approval behavior, including the final submission race, is not yet
live-tested.
