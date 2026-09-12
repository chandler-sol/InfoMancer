# 0.9 Cycle 0A: Alpha Reseed

The active 0.9 development line is being rebuilt from the completed `release/0.8.1-beta.2` baseline instead of merging the legacy 0.9 branch wholesale.

## Lineage

- Finished baseline: `release/0.8.1-beta.2`
- Legacy 0.9 head: `bf19099681f1cf86d59446cecb3b12e2aea49c6a`
- Preserved legacy branch: `archive/0.9-alpha-pre-reseed`
- Reseeded working branch: `testing/0.9-alpha-reseed`

The legacy branch diverged before a large amount of 0.8 work landed. It remains available as a source of individual features, tests, and design work, but it is not a safe merge source.

## Port rules

1. Start every transplant from the Beta 2 implementation.
2. Prefer small feature groups over whole-file replacement.
3. Do not reuse legacy migration numbers. Beta 2 currently owns migrations through 17, so new 0.9 schema work starts at 18 or later.
4. Treat workflows, security code, desktop startup code, templates, and provider integrations as compare-and-adapt work because Beta 2 contains newer fixes.
5. Keep media analysis read-only unless a feature is explicitly part of an existing reviewed write workflow.
6. Run CI after each coherent transplant group before bringing forward the next one.

## Initial transplant order

1. Re-establish the 0.9 roadmap and branch identity.
2. Port the 0.9 intelligence schema foundation using new migration numbers.
3. Port stream inventory and media-integrity services with their focused tests.
4. Review managed FFprobe/runtime tooling against the current Beta 2 desktop bundle.
5. Review matching and TVDB recovery work.
6. Review CSP/security acceptance work and keep only behavior not already superseded.
7. Review desktop startup/recovery changes last because that area changed heavily during 0.8.

## Completion criteria

Cycle 0A is complete when the canonical 0.9 development branch descends from Beta 2, the old branch remains recoverable, selected 0.9 work has been transplanted in reviewable groups, and the resulting test suite is green.