# Issue tracker: GitHub

Issues and PRDs for this repository live in GitHub Issues. Use the `gh` CLI for creating,
reading, updating, labeling, commenting on, and closing issues. Infer the repository from the
current Git remote.

## Conventions

- Create an issue with `gh issue create`.
- Read an issue and its comments with `gh issue view <number> --comments`.
- List and filter issues with `gh issue list` and its JSON output options.
- Comment with `gh issue comment <number>`.
- Apply or remove labels with `gh issue edit <number>`.
- Close an issue with `gh issue close <number>`.

## Source-of-truth boundary

GitHub Issues are the execution tracker, not a second product specification. Product behavior and
acceptance baselines live in `docs/specification.md`; engineering boundaries live in
`docs/architecture.md`, accepted ADRs, and `AGENTS.md`.

Each implementation issue should contain only:

1. its parent issue or milestone;
2. links to the relevant canonical specification or architecture sections;
3. the outcome and scope of this task;
4. acceptance criteria specific enough to verify the task; and
5. dependencies or blockers.

Do not copy the full product specification into an issue. If an issue intentionally changes a
canonical decision, update the canonical document and the issue together. If they conflict, stop
implementation, report the conflict, and wait for the sources to be reconciled and reviewed.

## Pull requests as a triage surface

Pull requests as a request surface: **no**.

GitHub shares one number space across issues and pull requests. When a bare reference such as
`#42` is ambiguous, try `gh pr view 42` and then `gh issue view 42`.

## Skill operations

When a skill says "publish to the issue tracker", create a GitHub issue. When a skill says
"fetch the relevant ticket", read the GitHub issue and its comments.

Wayfinder maps and child tickets also use GitHub Issues, native sub-issues, and native issue
dependencies when available. If those features are unavailable, use a task list in the map issue
and a `Blocked by: #<number>` line in the child issue.
