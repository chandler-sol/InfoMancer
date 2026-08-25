# InfoMancer 0.9 Roadmap: Intelligence & Automation

0.9 builds upward from the safe Workspace and filesystem foundation established in 0.8. The first development branch is deliberately isolated from the 0.8 release candidate line.

## P0: MIE 2.0

- Track opened and resolved findings per analysis run.
- Persist per-title health snapshots and surface titles that need attention.
- Expand explainable anomaly detection and library goals instead of introducing opaque scores.
- Keep all MIE analysis read-only with respect to media files.
- Add richer trends, health history, and explanations for score changes over subsequent alphas.

## P0: Deep Media Integrity / MESF

- Add an explicit read-only FFmpeg decode-sampling pass.
- Persist pass/warning/failure evidence and feed reproducible problems into MIE Review.
- Re-check files after size or modification changes.
- Never attempt automatic repair, remux, replacement, or deletion.
- Add optional full-decode verification and more precise timestamp/truncation diagnostics in later 0.9 alphas.

## P0: Audio & Subtitle Intelligence

- Persist every FFprobe audio and subtitle stream rather than only the first audio track.
- Record language, codec, channels/layout, title, default/forced state, accessibility flags, and commentary hints.
- Let Librarians define explicit language/subtitle/channel goals.
- Aggregate gaps at title/series scope so a TV season does not create hundreds of noisy findings.
- Grow toward Atmos/DTS:X detection, external subtitle awareness, forced-subtitle logic, and per-library/source policy overrides.

## P1: Task Context & Deep Linking

- Make active task rows in the **Tasks & notifications** widget clickable so selecting a task opens the InfoMancer surface where that work is running or was started.
- Preserve useful task context in the destination when practical, such as the affected source, title, maintenance tool, review queue, or scheduled-task definition.
- When a task has no dedicated live surface, fall back to the most relevant Activity, Operations, or Scheduled Tasks view instead of making the row a dead end.

## P1: Realistic Tour Demo Media

- Replace sparse tour placeholders with a richer synthetic media fixture set that looks like a believable populated InfoMancer library without exposing or depending on real user media.
- Give tour titles realistic fake metadata such as year, runtime, genres, overview, studio/network, content rating, provider IDs, poster/backdrop references, series/season/episode structure, and match confidence.
- Generate believable technical media facts including container, codecs, resolution, HDR/SDR state, bitrate, file size, audio tracks, subtitle tracks, runtime, and sample source/file paths.
- Include realistic InfoMancer-specific state such as tags, collections, favorites, organization status, source assignment, MIE findings, health history, duplicate hints, and operation history where the tour demonstrates those surfaces.
- Seed a mix of healthy and intentionally imperfect examples so Inspector, Review, MIE, organization tools, filters, and quick actions can demonstrate meaningful states instead of empty cards.
- Keep every tour record unmistakably synthetic internally, deterministic between runs, isolated from the real database/filesystem, and safe for screenshots, demos, automated tests, and first-run onboarding.
- Prefer a reusable fixture generator over one-off hard-coded titles so future tour steps can request consistent fake movies, series, seasons, episodes, and media variants.

## P1: Tour Viewport & Target Positioning

- Make every tour step deliberately position the page so the feature being explained is fully visible instead of inheriting an arbitrary scroll position from the previous step.
- Scroll the target section into view with enough top and bottom breathing room to keep headings, controls, status cards, and explanatory text visible at the same time.
- Account for the tour card itself when choosing a scroll position so the overlay does not cover the control or section it is describing.
- Prefer stable anchor/target-based positioning over fixed pixel offsets, since panel heights and content can change as features evolve.
- Recalculate placement for different viewport sizes and desktop-window dimensions rather than assuming a single screenshot-sized layout.
- Avoid jumps that leave section headings clipped at the top edge or position the highlighted feature partially off-screen, especially on longer settings and maintenance pages.
- Preserve or restore the user's previous scroll position when the tour is skipped, closed, or completed where doing so does not create confusing navigation.
- Add tour regression coverage for representative short and long pages so future layout changes cannot silently break step positioning.

## P1: Desktop Startup Experience

- After an InfoMancer desktop installation has been configured, remember its selected target and launch directly into it instead of asking Local Desktop versus Server Client on every startup.
- Keep the Local Desktop versus Server Client chooser for first run, recovery, or an explicit **Change installation** action rather than making it the normal launch screen.
- For bundled local-core startup, show a lightweight animated InfoMancer splash while the core, database, and Workspace become ready. The splash must hide startup latency without artificially delaying a fast launch.
- Avoid fake percentage progress. Prefer honest phase text such as **Starting InfoMancer**, **Opening catalog**, and **Preparing Workspace**, with an indeterminate animation while each phase is active.
- Delay the splash briefly before showing it so very fast launches do not flash a window unnecessarily.
- If startup exceeds the normal window, transition the splash into a useful slow-start state with access to diagnostics or retry information rather than silently waiting forever.
- Keep actual startup failures visible and actionable. The splash is presentation for legitimate startup work, not a way to conceal errors.
- Preserve a route back to installation selection from Settings or the desktop launcher so changing how the app connects never requires deleting application data.

## P2: Appearance & Themes

Keep InfoMancer's canonical appearance dark and OLED-first while making the UI adaptable to different environments and accessibility needs.

- Make **InfoMancer OLED** the default theme with true-black canvas areas and carefully elevated dark surfaces.
- Add a small curated theme set rather than a large theme marketplace:
  - **OLED**: true black, canonical InfoMancer appearance.
  - **Graphite**: softer neutral dark surfaces.
  - **Midnight**: very dark blue-black surfaces.
  - **Light**: intentionally designed light surfaces rather than a simple color inversion.
  - **System**: follow the operating system/browser light or dark preference.
- Move remaining hard-coded presentation colors toward semantic CSS variables such as canvas, surface, raised surface, text, border, accent, and semantic status tokens.
- Keep status meaning stable across themes: critical/error, warning, healthy/success, and informational colors must remain recognizable and accessible.
- Persist appearance per user so different accounts can choose different themes without changing the installation globally.
- Keep theme selection separate from any future accent-color selection so appearance and brand accents do not multiply into dozens of theme combinations.
- Treat this as a lightweight 0.9 UI feature built primarily through CSS tokens and a small persisted preference, not a new rendering architecture.

## Future: Multiple Installation Profiles

The 0.9 startup work should keep the connection target model simple enough to grow into multiple saved InfoMancer installations later without making 0.9 depend on a full multi-instance manager.

- Allow the desktop shell to save named installations such as **Home**, **Server**, **Test**, or **Remote**, each pointing to either the bundled local instance or an explicitly configured remote InfoMancer server.
- Add a **Switch installation** action inside the desktop app so moving between saved instances does not require returning to a startup chooser or re-entering server URLs.
- Remember a default or last-used installation and open it automatically at launch.
- Show enough identity information to make targets unmistakable, such as friendly name, hostname, URL, connection type, and online/offline state.
- Keep authentication sessions and sensitive connection material isolated per installation. A login to one InfoMancer instance must never implicitly authorize another.
- Treat each installation as a separate catalog and operational context. Do not merge libraries, tasks, settings, or findings across instances unless a future feature explicitly introduces cross-instance behavior.
- Provide a safe way to edit, remove, or re-authenticate a saved installation without affecting the server itself.
- Consider optional LAN discovery later, but never auto-connect to an unknown server merely because it appears on the network.

## Next 0.9 priorities

After the three P0 intelligence systems stabilize: automation rules, notification destinations, read-only Plex/Jellyfin/Emby comparison, provider abstraction, passkeys/TOTP MFA, the desktop startup experience, and the lightweight appearance/theme pass above.

## Long-range 2.0 direction

A privacy-first, explainable algorithmic recommendation engine remains intentionally out of scope for 0.9. The long-range design should learn from explicit ratings/favorites and opt-in playback history, recommend both owned titles and discovery candidates, expose why each item was recommended, and include a Familiar-to-Adventurous discovery control. The preference model should remain local whenever practical.
