# 0.8.1 Collection stabilization

This release-only stabilization pass covers five reported regressions without expanding the 0.8 feature surface beyond the requested Library organization workflow.

- Undo validation no longer depends on mapped-drive `realpath` succeeding when lexical containment and the existing catalog/file guards can safely validate a recorded rename.
- Smart Collection creation uses the available desktop width more intentionally and keeps collection cards from collapsing into overly narrow columns.
- Collection Sort Titles controls receive explicit label spacing and an overflow-safe menu scope, including a runtime marker for alpha markup variants.
- Deleting a Collection records a complete one-shot catalog snapshot and presents a persistent Undo control. Restoring recreates Smart rules and manual title/episode ordering without touching media files.
- Library multi-selection exposes Add to Collection for adding the selected titles to one or more manual Collections, with optional inline creation of a new manual Collection. Smart Collections remain rule-driven.

The Collection delete Undo intentionally preserves custom collection artwork so an immediate restore can recover the full Collection presentation. A later retention/cleanup pass can garbage-collect artwork that no longer belongs to any Collection after the undo window is no longer relevant.
