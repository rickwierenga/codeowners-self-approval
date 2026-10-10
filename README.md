# CODEOWNERS self-approval

A required **Code ownership** check lets managers merge PRs for their assigned
paths. The author counts as an owner; other owners approve using normal GitHub
reviews. Ownership comes from `.github/CODEOWNERS`, including organization teams.
Anyone with Write can click Merge after the requirements pass.

## Setup

1. Register an organization-owned GitHub App with **Checks: write**, **Contents:
   read**, **Pull requests: read**, and organization **Members: read**. Disable
   webhooks and install it on the repository. No server or extra user account.
2. Create environment `ownership-policy`. Allow only **Branch: main**, with no
   tag rules. Store its key as environment secret `OWNERSHIP_APP_PRIVATE_KEY`
   and its ID as environment variable `OWNERSHIP_APP_ID`. Managers must not have
   permission to change the environment or branch protection.
3. Copy [the workflow](examples/owner-approval.yml), which pins the action revision.
4. In your existing `Unit Tests` workflow, add `pull_request_review:` under `on`
   and `if: github.event_name != 'pull_request_review'` to each test job. Its
   completion signals review changes to the trusted workflow without rerunning
   tests. Give skipped jobs a distinct name on review events so they do not replace
   real test results. If your workflow has another name, update `workflows`.
5. Once tested on GitHub, require **Code ownership** from **this specific App**
   in `main` protection. Require branches to be up to date and PRs for changes;
   set native required approvals to zero and disable native code-owner reviews.
   Preserve existing CI requirements. Managers get Write, without bypass.

The App identifies the required check so another writer cannot fake a passing
result using a different workflow. Its key is available only to trusted `main`
workflows. The action never executes PR code or submits approval reviews.

## Behavior

- Opening or updating a PR, changing reviews, and updating `main` recheck ownership.
- Every changed path needs its author or an approving owner. Reviews must cover
  the current head commit. Changes-requested reviews from writers block merging.
- Author authority requires an authenticated author event for that exact head;
  another person's push needs owner review or a fresh author event.
- Last matching CODEOWNERS rule wins. Supported: `*`, anchored files/directories,
  and `*` or `?` in the final component. Other syntax fails closed.
- Renames check both paths. API failures fail the check. Membership changes take
  effect on the next evaluation; they do not themselves trigger a workflow.
- GitHub checks are asynchronous: review revocation and check completion are not
  atomic. Runs queue sequentially (GitHub limits the pending queue to 100).

Validated with 21 local ownership/review checks and a successful live GitHub
Actions evaluation of 42 open PRs using an organization App. Author and review
event scenarios were tested locally; no test PR was merged.
