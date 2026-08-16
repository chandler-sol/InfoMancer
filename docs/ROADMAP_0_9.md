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

## Next 0.9 priorities

After the three P0 intelligence systems stabilize: automation rules, notification destinations, read-only Plex/Jellyfin/Emby comparison, provider abstraction, and passkeys/TOTP MFA.

## Long-range 2.0 direction

A privacy-first, explainable algorithmic recommendation engine remains intentionally out of scope for 0.9. The long-range design should learn from explicit ratings/favorites and opt-in playback history, recommend both owned titles and discovery candidates, expose why each item was recommended, and include a Familiar-to-Adventurous discovery control. The preference model should remain local whenever practical.
