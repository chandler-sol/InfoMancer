<!-- graft:start -->
## Graft — repo context graph

This repo is indexed in `graft/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `graft map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `graft ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `graft grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `graft skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `graft callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `graft/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `graft ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `graft build` (deterministic,
no API key, $0).
<!-- graft:end -->

## 0.8 UI ownership rules

Interactive application surfaces have one canonical controller. Do not add inline
compatibility controllers or a second listener that mutates the same state to work
around an ordering bug. Fix the canonical owner or its load boundary instead.

- `app/static/app-shell.js` owns global search, the site menu, sidebar interaction,
  flash cleanup, and CSRF injection for native POST forms. `app-shell-bootstrap.js`
  may only restore first-paint shell geometry.
- `app/static/task-widget.js` owns `/api/tasks` polling, task/failure/scheduled state,
  bell state, task popover rendering, and the onboarding task-demo handoff.
- `app/static/library-controller.js` owns Library filter/search AJAX and canonical
  title-selection state. Other Library enhancements consume its events rather than
  maintaining a second selection model.
- `app/static/library-surface-lazy.js` exclusively owns List/Covers state, the
  `infomancer_library_view` cookie, view persistence, and lazy surface hydration.
- `app/static/library-density.js` owns cover density only. It must not switch views.
- `app/static/workspace-core.js` owns Inspector open/close behavior. Selection polish
  may influence selection UX but must not intercept the core same-title toggle.
- `app/static/app-navigation.js` owns navigation pending state and prefetch only. It
  must not become a second owner for global search or Library view state.
- `app/templates/base.html` and `app/templates/library.html` are markup/state seeds,
  not JavaScript controller hosts. Keep them free of inline controller scripts.

When changing one of these surfaces, extend `tests/test_08_release_ui_gremlins.py`
with an ownership contract when practical. Prefer server-rendering the correct first
state and then enhancing it, rather than painting a legacy state and correcting it
later with JavaScript.
