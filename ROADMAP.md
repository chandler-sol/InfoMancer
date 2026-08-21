# InfoMancer Roadmap

InfoMancer is currently on the **0.8 alpha** development line. The major 0.8 product architecture is in place; the remaining work is primarily qualification, release engineering, durability testing, accessibility, performance validation, and final polish.

This roadmap describes the intended path from the current alpha through 0.8 Stable, then identifies candidate themes for 0.9 and the trust/stability bar for 1.0.

It is a planning document, not a promise that every post-0.8 candidate will ship in the listed release. The current release gate and qualification matrix remain the source of truth for whether 0.8 is ready to promote.

## Current position

### 0.8 Alpha: feature-complete or very close

The core 0.8 Workspace roadmap is complete:

- W1 Workspace foundation and stabilization
- W2 Library Inspector
- W3 Unified Review Workspace
- W4 reusable drawers, dialogs, toasts, partial interactions, keyboard shortcuts, and command palette
- W5 Saved Views
- W6 Operation History and guarded Undo

The broader 0.8 product now includes:

- local-first Movie and TV cataloging across multiple sources;
- TheTVDB and IMDb-backed matching and metadata;
- TV expected-episode and missing-episode intelligence;
- FFprobe-backed technical inspection;
- Media Intelligence Engine findings with evidence and recommendations;
- duplicate detection, hash verification, managed Trash, and guarded restore;
- preview-first rename and season-folder organization;
- Read-Only, Standard, and Lockdown filesystem protection modes;
- Saved Views, Favorites, ratings, tags, Collections, Smart Collections, and Custom Libraries;
- recovery packages, database backups, exports, and restore tooling;
- Librarian/Member accounts, local authentication, sessions, invitations, recovery, and optional Cloudflare Access integration;
- Docker deployment plus an evolving native Windows application and installer/update architecture.

At this point, 0.8 should resist broad new feature work unless a missing capability is necessary to make an existing 0.8 workflow complete, safe, or understandable.

---

## 0.8 Beta

**Goal:** stop proving that features exist and start proving that the complete product behaves correctly under normal and abnormal use.

### Entry criteria

0.8 can move from Alpha to Beta when:

- normal manual use is no longer uncovering structural navigation, data-model, or workflow redesigns;
- the major 0.8 flows can be completed end to end without known data-loss or destructive-action defects;
- the Library, Inspector, Review, Sources, Settings, Recovery, Activity, accounts, and native Windows surfaces have completed a focused visual-polish pass;
- current automated tests are green across supported CI platforms;
- any known Alpha-only shortcuts or temporary UX are documented or removed.

### Beta priorities

1. **Filesystem qualification**
   - destination collisions;
   - source disconnects and NAS/share loss;
   - permission changes before and during writes;
   - symlink/junction boundary behavior;
   - case-only renames;
   - Windows reserved names and long paths;
   - concurrent scans and filesystem actions;
   - Undo and restore after state drift.

2. **Data durability**
   - process termination during scans and metadata writes;
   - WAL recovery;
   - disk-full behavior;
   - corrupted backup/recovery inputs;
   - interrupted backup and portable restore;
   - rollback at recovery commit boundaries;
   - restoration from every historical schema that 0.8 claims to support.

3. **Large-library performance**
   - benchmark approximately 1k, 10k, 50k, and 100k files;
   - measure initial and incremental scans;
   - measure Library search, Inspector/detail aggregation, Review queries, hashing queues, backups, and recovery-package creation;
   - record memory usage and network-storage behavior;
   - establish release budgets and investigate material regressions.

4. **Accessibility and responsive QA**
   - keyboard-only operation;
   - visible focus and focus return;
   - 200% zoom;
   - reduced motion;
   - screen-reader testing;
   - phone, tablet, 1366x768, 1080p, 1440p/4K, and ultrawide layouts;
   - long titles, paths, credits, metadata, and localized-length text;
   - elimination of unintended page-level horizontal overflow.

5. **Security and privacy verification**
   - final Member-versus-Librarian authorization matrix;
   - direct URL/API/crafted-form permission testing;
   - realistic secret, token, cookie, email, path, diagnostic, log, and export review;
   - repository secret-scanning verification and response procedure.

### Beta rule

During Beta, feature additions should be exceptional. Fixes, usability improvements, performance work, missing error handling, and safety improvements take priority over expanding scope.

---

## 0.8 Release Candidate

**Goal:** produce a build that could become 0.8 Stable without code changes other than release-blocking fixes.

### RC entry criteria

- filesystem torture matrix passes on the supported Windows and Linux paths plus at least one real network share;
- data-durability matrix passes;
- large-library benchmark results and budgets are recorded;
- accessibility/responsive qualification has no critical defects;
- clean install, upgrade, backup, uninstall, reinstall, and restore cycles are proven on claimed platforms;
- supported OS, architecture, Docker, filesystem/share, and native-Windows requirements are explicit;
- final authorization and secret-redaction review passes;
- privacy statement, software license, third-party notices, provider review, and FFmpeg/FFprobe licensing review are complete;
- production release/update signing is configured and exercised;
- Windows publisher signing is configured for public native builds;
- release artifacts include SHA-256 checksums and retained provenance;
- release rollback and emergency security-release procedures have been rehearsed.

### RC policy

- No planned feature work.
- No broad visual redesigns.
- Database/schema changes require a release-blocking justification.
- Fixes should be narrowly scoped and followed by targeted regression testing.
- Every RC should be tested on clean machines, not only developer environments.

---

## 0.8 Stable

**Goal:** establish the first release on the 0.8 architecture that users can reasonably trust for everyday cataloging, analysis, review, organization, and guarded filesystem management.

0.8 Stable should mean:

- the documented supported environments have been tested rather than inferred;
- backup and recovery procedures have been exercised successfully;
- filesystem-changing operations fail closed when state is uncertain;
- migrations and supported upgrades are repeatable;
- normal large-library behavior is measured and bounded;
- keyboard/accessibility basics are treated as product requirements;
- installers, updates, checksums, and signatures are production-ready;
- privacy, provider, dependency, and licensing obligations are documented;
- known limitations are explicit in release notes.

After 0.8 Stable, the 0.8 branch should favor maintenance and compatibility over architectural churn.

---

## 0.9: candidate product-expansion themes

0.9 should be defined after real 0.8 usage reveals which workflows deserve the next layer of investment. The items below are **candidates**, not committed scope.

### Authentication expansion

Already deferred beyond the current 0.8 release line:

- native passkeys;
- application-native MFA;
- direct Google, Microsoft, Apple, and GitHub sign-in adapters.

Any authentication expansion should preserve local-first operation and should not require an InfoMancer-hosted cloud account.

### MIE and Review evolution

Potential directions after MIE has enough real-world feedback:

- calibrate finding thresholds and confidence using observed false-positive/false-negative feedback;
- improve recommendation quality and prioritization;
- make related findings easier to understand as one remediation story;
- expand safe, explicitly approved batch remediation where filesystem protection modes and rollback semantics make that trustworthy;
- improve source-level and whole-library trend visibility without reducing explainability to one opaque score.

### Native application maturity

After the Windows alpha and 0.8 release pipeline are proven:

- improve desktop-native filesystem integration and diagnostics;
- harden update/recovery UX from real installations;
- reduce differences between Docker/web and native workflows where doing so improves usability;
- evaluate additional native platform shells only after Windows maintenance cost and release reliability are understood.

### Performance and scale follow-through

Use the 0.8 benchmark data to guide targeted work rather than optimizing speculatively:

- faster incremental scans;
- more efficient large Review queues and Inspector aggregations;
- reduced memory pressure during metadata, hashing, and analysis jobs;
- improved behavior on slow NAS/network storage;
- clearer operator visibility into long-running work.

### Integration and workflow expansion

0.9 may expand integrations only where they reinforce InfoMancer's catalog, intelligence, review, and safe-management role. Integrations should continue to respect the project's intentional boundary against automatically acquiring copyrighted media.

Candidate expansion:

- allow multiple user-configured external search providers, with a deliberate provider chooser and ordering instead of replacing the current single-provider shortcut.

Specific integration scope should be chosen after 0.8 rather than committed prematurely.

---

## 1.0: the trust contract

1.0 should not be defined by the number of features. It should be the point where InfoMancer's core contracts are stable enough to support long-term use.

### Proposed 1.0 bar

- a documented compatibility policy for databases, recovery packages, settings, and upgrades;
- a clear support window and policy for unsupported old schemas;
- stable backup/restore semantics with tested cross-version recovery;
- supported platforms and architectures explicitly maintained;
- signed and reproducible release artifacts with a rehearsed security-update process;
- accepted performance budgets for large libraries;
- accessibility baselines treated as release requirements;
- mature Member/Librarian authorization and secure defaults;
- finalized privacy, license, third-party, provider, and redistribution documentation;
- release notes and migration guidance sufficient for users to upgrade without repository knowledge;
- no known class of issue that can silently overwrite, misplace, or permanently delete user media under supported workflows.

Feature work can continue before 1.0, but reaching 1.0 should prioritize **predictability, recoverability, compatibility, and trust** over accumulating another large feature list.

---

## What is deliberately not on the roadmap

InfoMancer is not intended to become an automated media-acquisition client. The project does not plan to scrape torrent-result pages, automatically acquire copyrighted media, or submit downloads to a downloader. External search links remain user-directed convenience links.

InfoMancer also should not make destructive filesystem decisions solely from an opaque intelligence score. MIE can identify, explain, prioritize, and recommend; media-changing actions should remain reviewable and protected by explicit safety rules.

---

## Roadmap source documents

Use these documents alongside this roadmap:

- [`docs/WORKSPACE.md`](docs/WORKSPACE.md) for the completed 0.8 Workspace architecture and W1-W6 history.
- [`docs/RELEASE_REVIEW.md`](docs/RELEASE_REVIEW.md) for the authoritative 0.8 promotion gate.
- [`docs/QA_0_8.md`](docs/QA_0_8.md) for repeatable manual qualification matrices.
- [`docs/reference/FEATURE_CATALOG.md`](docs/reference/FEATURE_CATALOG.md) for the detailed capability inventory.
- [`SECURITY.md`](SECURITY.md) for the supported security model and reporting guidance.

When these documents disagree, implementation and the current release gate should be reconciled before promotion rather than allowing roadmap/documentation drift to become permanent.
