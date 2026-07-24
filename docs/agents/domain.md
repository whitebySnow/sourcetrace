# Domain Docs

SourceTrace uses a single-context domain documentation layout.

## Before exploring

- Read `CONTEXT.md` at the repository root when it exists.
- Read ADRs under `docs/adr/` that affect the area being changed.
- If these files do not exist, proceed silently. Do not create speculative domain documentation.

The `domain-modeling` skill creates or updates domain documentation when terminology or decisions
are actually resolved.

## Use the glossary vocabulary

Use terms as defined in `CONTEXT.md` when naming domain concepts in issues, proposals, tests, and
code. If a required concept is missing, reconsider whether the new term is necessary or record the
gap for domain modeling.

## Flag ADR conflicts

If proposed work conflicts with an existing ADR, surface the conflict explicitly instead of
silently overriding the recorded decision.
