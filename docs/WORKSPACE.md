# InfoMancer Workspace

InfoMancer 0.8 starts the transition from a page-oriented management website to a persistent media-operations workspace.

## Product rules

- Preserve context. Selecting media should not immediately replace the working view.
- Single click inspects. Double click or Enter opens full details.
- Keep background work visible without forcing navigation.
- Small actions belong in popovers/dialogs, medium actions in drawers, and deep workflows in full workspace views.
- Every important state keeps a real URL and progressive server-rendered fallback.
- Avoid a framework rewrite. FastAPI and Jinja remain the application foundation.

## Navigation model

Primary work domains are Dashboard, Library, Review, Sources, and Activity. Existing capabilities remain available as secondary destinations beneath Library, Review, and System groupings.

## Workspace phases

1. **W1 Foundation**: shared workspace styles, navigation hierarchy, contextual bulk-action toolbar, first persistent Library inspector.
2. **W2 Library**: server-backed inspector partials, richer file/edition/quality information, history-aware selection state, instantaneous favorite/tag actions.
3. **W3 Review**: unified queue for MIE findings, duplicates, unmatched media, missing episodes, metadata issues, and quality decisions.
4. **W4 Interaction**: reusable drawers, dialogs, toasts, partial navigation, keyboard shortcuts, and command palette.
5. **W5 Saved Views**: named filter/sort workspaces that can be pinned to Library and Dashboard.
6. **W6 Operations**: generalized operation history and reversible actions where filesystem semantics permit safe undo.

W1 intentionally uses the existing rendered Library DOM as its inspector data source. W2 should replace that prototype with a dedicated read-only inspector endpoint/partial so the panel can expose richer metadata without duplicating page logic in JavaScript.
