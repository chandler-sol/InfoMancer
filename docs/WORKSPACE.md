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

1. **W1 Foundation + stabilization (complete)**: server-rendered workspace shell, collapsible navigation hierarchy, intentional compact rail, contextual bulk-action toolbar, cohesive title dossier, local-library people previews, and the first persistent Library inspector.
2. **W2 Library (complete)**: server-backed inspector partials, richer file/edition/quality information, history-aware selection state, instantaneous favorite/tag actions.
3. **W3 Review (complete)**: unified queue for MIE findings, duplicates, unmatched media, missing episodes, metadata issues, and quality decisions.
4. **W4 Interaction (complete)**: reusable drawers, dialogs, toasts, partial navigation, keyboard shortcuts, and command palette.
5. **W5 Saved Views (complete)**: named filter/sort workspaces that can be pinned to Library and Dashboard.
6. **W6 Operations (complete)**: generalized operation history and reversible actions where filesystem semantics permit safe undo.

W1 intentionally uses the existing rendered Library DOM as its inspector data source. W2 replaces that prototype with a dedicated read-only inspector endpoint/partial so the panel can expose richer metadata without duplicating page logic in JavaScript.

## W1.5 Application decomposition

W1.5 moves the product/domain HTTP surface out of `app/main.py` into `app/routes/` APIRouter modules. The composition root retains application construction, middleware, lifecycle, bootstrap/authentication, and admin-account wiring. Existing handler names are published as compatibility aliases during the transition. Route-level Librarian dependencies remain attached inside each router module. Runtime service references remain live through `RouteContext`, preserving test isolation and future service replacement without circular imports.

The completed decomposition moves 158 of 192 route functions into eight domain routers: `system`, `operations`, `dashboard`, `review`, `library`, `settings`, `collections`, and `titles`. The remaining 34 routes are intentionally concentrated in the composition root because they cover bootstrap, authentication, account/admin, engagement, or closely related application wiring. `app/main.py` drops from 8,144 lines to 3,170 lines without changing the public route surface.

W1.5 adds architecture-contract tests for router presence, compatibility handler aliases, live service replacement, and preservation of route-level Librarian authorization. The completed tree passes 193 tests plus `python -m compileall app`. New Workspace endpoints should be added to the appropriate router rather than growing `main.py` again.

## W2 Library Inspector

W2 replaces the W1 DOM-scraping Inspector with a dedicated read-only `/library/inspector/{title_id}` partial. The Inspector now exposes catalog identity, provider IDs, source health, MIE findings, missing episodes, duplicate review counts, media characteristics, editions/versions, organization state, and indexed file details without performing provider network work on GET. Personal favorite and existing-tag toggles use small CSRF-protected JSON mutations while the full server-rendered organization flows remain available as fallbacks. Library title selection persists for the current library view across reloads and live result replacement, and Inspector state is represented by the `inspect` query parameter so browser history can restore the current inspected title.

### W2 validation

The final W2 application tree passes 198 tests plus `python -m compileall app`. Coverage includes server-rendered Inspector data, read-only GET behavior, member-safe CSRF-protected favorite/tag mutations, route authorization, keyboard and range selection contracts, browser-history restoration, aggregate media totals beyond the bounded file preview, and correct runtime handling for alternate movie editions. The permanent repository matrix also passes dependency audit plus Python 3.13 tests and compilation on Ubuntu, macOS, and Windows.

## W5 Saved Views

W5 turns the current Library filter/sort state into a personal reusable workspace.
Signed-in users can save the normalized current Library, Movies, or TV Shows view,
rename it, pin or unpin it, and delete it without affecting media or global settings.
Only the known Library filter keys are stored; arbitrary query parameters and external
paths are discarded before persistence. Pinned views appear both above the Library
filters and on Dashboard. Saved views are private to each account and capped to keep
the navigation surfaces manageable.

The full TV title view also gains an installation-wide collapsed/expanded default for
season groups. Collapsed remains the default, while Librarians can switch the starting
state under General Settings; per-page Expand all and Collapse all controls remain
available.

## Portable Recovery Package

System Settings can create a single `.infomancer-backup` file for disaster recovery
and future native-uninstall handoff. The package contains a consistent SQLite backup,
collection artwork, a versioned manifest, file sizes, and SHA-256 checksums. Creation
verifies the package before download, and an existing package can be uploaded for
verification without changing the live installation. Archive traversal, undeclared
entries, duplicate paths, checksum mismatches, oversized packages, invalid databases,
and unsupported format versions are rejected.

The recovery format never contains movie or TV media, provider credentials or their
local encryption keys, deployment environment files, application binaries, or caches.
This keeps the package portable without weakening provider-secret encryption.

## Persisted Global Rename Review

Rename suggestions are now stored as background-generated filesystem snapshots instead
of being recomputed from disk whenever Review loads. Librarians can explicitly refresh
the snapshot, then use the **Renames** Review bucket to inspect ready, blocked,
dismissed, and resolved suggestions across the whole catalog. The saved proposal
records source/destination paths plus file size and nanosecond modification time.

Applying a proposal revalidates the catalog path, configured-source boundary, source
signature, and destination collision before changing anything. A stale or occupied
proposal fails closed and must be refreshed. Successful renames update the catalog and
flow into W6 Operation History for guarded undo. Read-Only Mode blocks apply but never
blocks proposal generation or review.

## Season Folder Organization

Full TV title pages include a preview-first season-folder workflow. Parsed episode
files can be moved into `Season 01`, `Season 02`, and equivalent folders, while season
zero maps to `Specials`. The preview does not create directories or alter media.
Unparsed files remain untouched, existing destinations block the proposal, and apply
revalidates each source and destination before moving anything. Old empty folders are
left in place rather than being deleted automatically.

Completed moves update the catalog and are written to W6 Operation History as ordinary
file moves, so the same guarded undo checks can restore the previous path. Global
Read-Only Mode blocks apply while still allowing the preview.

## Global File Protection Modes

InfoMancer exposes three mutually exclusive installation-wide media safety modes.
**Read-Only Mode** blocks every InfoMancer operation that renames, moves, restores,
or permanently deletes user media while leaving scans, matching, inspection, MIE,
metadata, tags, collections, and application/database maintenance available.
**Standard Mode** permits reviewed filesystem changes. **Lockdown Mode** permits
reviewed reversible changes while pausing automatic permanent managed-trash deletion
and reserving stronger confirmation for irreversible actions.

The media-write boundary is enforced server-side through one FileProtectionService and
not only by hidden or disabled controls. Current rename paths, managed-trash move and
restore, W6 undo, and scheduled permanent trash cleanup all consult that boundary.
A persistent Librarian banner makes Read-Only state visible throughout the workspace.

## W6 Operation History + Safe Undo

W6 adds a durable Librarian-only operation ledger for filesystem changes. Episode,
movie, and show-folder renames record their before/after paths, while duplicate files
moved into managed Trash record the existing reversible trash item. Activity links to
a dedicated Operation History view with status/type filtering and clear undo state.

Undo is deliberately narrow. InfoMancer never stores or replays arbitrary commands.
Before reversing a file or folder rename it verifies the current catalog path, source
boundary, expected filesystem object, destination collision state, and original parent
folder. Managed-trash undo delegates to the existing guarded restore workflow. If any
state has drifted, undo fails closed and records the reason while leaving the operation
available for review. Manual managed-trash restores also mark their originating move as
undone. Synthetic auth-disabled identities are recorded as system actions instead of
creating invalid user references.

### W5 + W6 validation

The integrated 0.8 tree exercises saved-view isolation and query normalization,
installation-wide TV season display defaults, file and folder rename undo, collision
refusal, managed-trash restore, synthetic auth-disabled actors, migrations, and the
Operation History interface. The W6 integration validation passes 229 tests plus
`python -m compileall -q app`; the permanent cross-platform CI matrix remains the
release gate for the merged `testing/0.8-alpha` branch.

## W3 + W4: Unified Review and application interactions

W3 makes `/review` the primary decision surface. It adapts existing MIE findings, live duplicate candidates, and failed metadata work into one filtered queue without introducing a second source of truth. Specialist pages remain available for deep workflows.

W4 adds reusable Workspace primitives: a server-backed right drawer, same-origin AJAX forms, confirmation dialogs, contextual menus, toasts, and a Ctrl/Cmd+K command palette. Review uses these primitives first, but they are intentionally generic so Library, Sources, and later Operation History can reuse them.

Review GETs remain read-only. Librarian-only state changes use dedicated CSRF-protected POST routes and preserve the route-level authorization boundary established during 0.7 hardening.
