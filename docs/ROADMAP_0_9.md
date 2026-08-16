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

## P2: Appearance & Themes

Keep InfoMancer's canonical appearance dark and OLED-first while making the UI adaptable to different environments and accessibility needs.

- Make **InfoMancer OLED** the default theme with true-black canvas areas and carefully elevated dark surfaces.
- Add a small curated theme set rather than a large theme marketplace:
  - **OLED** — true black, canonical InfoMancer appearance.
  - **Graphite** — softer neutral dark surfaces.
  - **Midnight** — very dark blue-black surfaces.
  - **Light** — intentionally designed light surfaces rather than a simple color inversion.
  - **System** — follow the operating system/browser light or dark preference.
- Move remaining hard-coded presentation colors toward semantic CSS variables such as canvas, surface, raised surface, text, border, accent, and semantic status tokens.
- Keep status meaning stable across themes: critical/error, warning, healthy/success, and informational colors must remain recognizable and accessible.
- Persist appearance per user so different accounts can choose different themes without changing the installation globally.
- Keep theme selection separate from any future accent-color selection so appearance and brand accents do not multiply into dozens of theme combinations.
- Treat this as a lightweight 0.9 UI feature built primarily through CSS tokens and a small persisted preference, not a new rendering architecture.

## Next 0.9 priorities

After the three P0 intelligence systems stabilize: automation rules, notification destinations, read-only Plex/Jellyfin/Emby comparison, provider abstraction, passkeys/TOTP MFA, and the lightweight appearance/theme pass above.

## Long-range 2.0 direction

A privacy-first, explainable algorithmic recommendation engine remains intentionally out of scope for 0.9. The long-range design should learn from explicit ratings/favorites and opt-in playback history, recommend both owned titles and discovery candidates, expose why each item was recommended, and include a Familiar-to-Adventurous discovery control. The preference model should remain local whenever practical.
