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

Primary work domains are Dashboard, Library, Review, Sources, and Activity. Existing capabilities remain available as collapsible secondary destinations beneath Library, Review, and More. The final navigation hierarchy is rendered by Jinja on the first response; JavaScript only coordinates interaction, so the shell does not repaint from a legacy menu after load.

## Workspace phases

1. **W1 Foundation + stabilization**: server-rendered workspace shell, collapsible navigation hierarchy, intentional compact rail, contextual bulk-action toolbar, cohesive title dossier, local-library people previews, and the first persistent Library inspector.
2. **W2 Library**: server-backed inspector partials, richer file/edition/quality information, history-aware selection state, instantaneous favorite/tag actions.
3. **W3 Review**: unified queue for MIE findings, duplicates, unmatched media, missing episodes, metadata issues, and quality decisions.
4. **W4 Interaction**: reusable drawers, dialogs, toasts, partial navigation, keyboard shortcuts, and command palette.
5. **W5 Saved Views**: named filter/sort workspaces that can be pinned to Library and Dashboard.
6. **W6 Operations**: generalized operation history and reversible actions where filesystem semantics permit safe undo.

W1 intentionally uses the existing rendered Library DOM as its inspector data source. W2 should replace that prototype with a dedicated read-only inspector endpoint/partial so the panel can expose richer metadata without duplicating page logic in JavaScript.

## W1.5 Application decomposition

W1.5 moves the product/domain HTTP surface out of `app/main.py` into `app/routes/` APIRouter modules. The composition root retains application construction, middleware, lifecycle, bootstrap/authentication, and admin-account wiring. Existing handler names are published as compatibility aliases during the transition. Route-level Librarian dependencies remain attached inside each router module. Runtime service references remain live through `RouteContext`, preserving test isolation and future service replacement without circular imports.

The completed decomposition moves 158 of 192 route functions into eight domain routers: `system`, `operations`, `dashboard`, `review`, `library`, `settings`, `collections`, and `titles`. The remaining 34 routes are intentionally concentrated in the composition root because they cover bootstrap, authentication, account/admin, engagement, or closely related application wiring. `app/main.py` drops from 8,144 lines to 3,170 lines without changing the public route surface.

W1.5 adds architecture-contract tests for router presence, compatibility handler aliases, live service replacement, and preservation of route-level Librarian authorization. The completed tree passes 193 tests plus `python -m compileall app`. New W2 Inspector endpoints should be added to the appropriate router rather than growing `main.py` again.
