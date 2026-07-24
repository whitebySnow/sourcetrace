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
