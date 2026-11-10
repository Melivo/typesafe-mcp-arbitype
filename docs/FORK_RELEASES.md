# Experimental fork releases

This is the release contract for `Melivo/typesafe-mcp-arbitype`, a fork of
`Renwang-Huang/arbitype`. The original MIT license, history, package name
`arbitype`, and compatibility entry points remain intact. Fork image/box
support is experimental; a GitHub prerelease is not a production-readiness
claim or a TypeSafe authorization decision.

## Version and release approval

Configuring this workflow does not create a tag, release, or version bump.
Before a future release, a maintainer must review the changes and explicitly
approve the version and tag publication. For `0.x`, fixes increment Patch;
features and breaking changes increment Minor and reset Patch to zero.
Mixed changes use the largest applicable increment. `1.0.0` requires a separate
explicit decision. There is no version-calculating bot or automatic bump.

Update the existing version sources consistently in a reviewed change, including
`arbitype/_version.py` and `server.json`; do not change package or Registry
ownership to make a release pass. Merge the reviewed change into trusted `main`
and wait for the **CI** push run on that exact commit to finish successfully.
Only then may the maintainer create and push an explicit `fork-vX.Y.Z` tag for
that commit. The tag version must equal the package version exactly, without
leading zeroes, suffixes, or an inferred next version. Ordinary pushes and PRs
do not publish fork releases; there is no manual-dispatch release shortcut.

## Current GitHub protections

As observed through the GitHub API on **2026-11-10**, four active repository
rulesets protect this release path:

- `main-integrity-and-ci` (26927034) applies to `main`: deletion and
  non-fast-forward updates are prohibited, all 17 required GitHub Actions
  status checks (integration ID `15368`) must pass on the latest commit, and
  there are no bypass actors.
- `main-reviewed-pull-requests` (26927035) applies to `main`: one approving
  review is required, stale approvals are dismissed, and review threads must
  be resolved. The `Melivo` account has a `pull_requests_only` bypass exception;
  this is not a universal independent-approval guarantee.
- `fork-release-tag-creation` (26927058) applies to `fork-v*` tags: the
  `Melivo` account is the sole configured bypass actor for the creation rule.
- `fork-release-tag-immutability` (26927060) applies to `fork-v*` tags:
  updates and deletions are prohibited, with no configured bypass actors.

These are the currently observed GitHub settings, not protections installed by
the workflow. Repository administrators can change rulesets, and this document
does not claim that YAML or the settings prevent an administrator from changing
the enforcement configuration or workflow. Recheck the live rulesets before a
future release; do not run destructive enforcement tests.

## Checks, artifacts, and permissions

`.github/workflows/fork-release.yml` accepts tag-push events only in the exact
fork repository. It validates the complete tag syntax, event ref/commit, peeled
tag commit, package version, and ancestry on `origin/main`. A successful,
completed `ci.yml` push run on `main` for the same commit is mandatory; a pending,
failed, cancelled, or missing run blocks publication. If CI was still pending
when the tag arrived, wait for success and explicitly rerun the failed workflow;
do not move the tag or bypass the gate.

The read-only build job repeats the provider-free unittest suite and Ruff,
then builds one wheel and its `SHA256SUMS` manifest. CI-only tooling/build
dependencies are installed on the GitHub runner, not added to the production
runtime. No TypeSafe call or stored API credential is needed. Tests/build have
no repository write or OIDC permission; checkout does not persist credentials.
Fully commit-pinned upload/download actions transfer the artifact between jobs
in the same run (retained for seven days).

Only the final job receives `contents: write`, plus `actions: read` for its CI
gate. It does not check out or execute package code, install tools, or run tests.
It rechecks the remote tag commit, main ancestry and CI result, verifies the
exact wheel/manifest file set and SHA256 against build-job outputs, and invokes
`gh release create` with `--verify-tag --prerelease --latest=false` and generated
notes. A missing tag fails instead of being implicitly created. The wheel and
`SHA256SUMS` are GitHub prerelease assets, not PyPI or Registry publications.
The only credential is the automatically scoped `GITHUB_TOKEN`.

Read the workflow logs and GitHub release page after a future run. Confirm the
tag/commit, prerelease flag, asset names and downloaded SHA256 before using an
asset. GitHub release creation and asset upload are not one atomic transaction;
after an error inspect the existing release/draft and assets before deciding
whether to retry. The workflow does not overwrite an existing release or use
asset `--clobber`. Do not assume a failed command left no remote state.

## Upstream publishing stays upstream

The existing PyPI, MCP Registry, and ecosystem-listing jobs require
`github.repository == 'Renwang-Huang/arbitype'`. Their original procedures,
OIDC configuration and identities are retained for upstream but skipped in
this fork, including manual and downstream workflow triggers. Fork prereleases
must not publish under the upstream owner's PyPI/Registry identity. The normal
CI remains enabled with read-only repository access. No current tag, GitHub
release, PyPI upload, Registry publish, or protection setting was created by
the automation setup.

## References

- [GitHub artifact transfer](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-what-your-workflow-does/storing-and-sharing-data-from-a-workflow)
- [GitHub CLI release creation](https://cli.github.com/manual/gh_release_create)
- [Supply-chain controls](SUPPLY_CHAIN.md)
- [Fork installation and experimental feature overview](../README.md)
