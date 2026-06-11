# 0032. Automated version bump on merge

- **Status:** Accepted
- **Date:** 2026-06-12

## Context

The versioning scheme is one integer, +1 per merged PR (`backend/app/_version.py`,
served at `/api/v1/version`). The bump used to live **in each PR**, which breaks as
soon as two PRs are open at once: both branch from the same version and both claim
the same next number. Git merges the identical one-line change silently, so two
releases ship calling themselves the same version — the failure is invisible. The
number's meaning ("how many PRs have merged") is only knowable at merge time, so
merge time is when it must be assigned.

## Decision

Move the bump out of PRs into CI: a **GitHub Action on every push to `main`**
(`.github/workflows/version-bump.yml`) increments `APP_VERSION` and commits the
result back. PRs never touch the version file.

- **No loops, no wasted CI:** the bump commit carries `[skip ci]`, which suppresses
  both this workflow and the CI workflow for that one-line commit.
- **Serialized:** a `concurrency` group queues rapid merges; each run re-reads the
  branch head before incrementing, so N merges net exactly +N.
- **Pushing past protection:** Actions' default token cannot bypass branch
  protection on a personal repository, and the GitHub Actions app isn't a valid
  ruleset bypass actor there. The workflow therefore authenticates with a
  repo-scoped **write deploy key** (`VERSION_BUMP_KEY` Actions secret), and the
  classic branch protection was migrated to an equivalent **ruleset** ("main
  protection": PR required, `backend`/`frontend`/`docker` checks, strict, no
  force-push/deletion) with **deploy keys as a bypass actor**.

## Consequences

- Parallel PRs are conflict-free and correctly numbered; the version is assigned at
  the only moment it's well-defined.
- `main`'s head is usually a bot bump commit without CI runs (skipped by design);
  the *content* commit beneath it is always CI-green via the PR checks. Deploys are
  manual (ADR-0027), so they naturally pick up the post-bump head.
- A push-capable credential now lives in Actions secrets. Its blast radius is this
  one repo; revoking is deleting the deploy key. Anything able to read repo secrets
  could push to `main` — accepted for a single-maintainer project.
- A PR that *does* touch `_version.py` won't break anything, but its change is
  immediately superseded; the convention (CLAUDE.md) says don't.

## Alternatives considered

- **Keep manual bumps, fix at merge** — reintroduces the human step that fails
  exactly when PRs are parallel; rejected.
- **Derive the version from git at build time** (`rev-list --count`) — zero
  credentials and zero commits, but needs `.git` inside the Docker build context,
  couples the number to commit count rather than PR count, and shows "dev" locally;
  rejected as more plumbing for a less faithful number.
- **PAT instead of a deploy key** — same risk profile but the credential acts as
  the owner everywhere it leaks; the deploy key is repo-scoped. Rejected.
