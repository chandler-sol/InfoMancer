# InfoMancer 1.0+ product backlog

This document captures post-0.8 feature directions that should be designed as cohesive product workflows rather than release-stabilization patches.

## Requested media and shared Wishlist

Build one shared acquisition Wishlist that supports both Librarian-created entries and Guest requests.

### Guest request flow

- A Guest can search configured metadata providers, starting with TVDB and remaining provider-neutral for future sources.
- A Guest can request a movie or TV series.
- The request records the requester and enters the shared Wishlist/request queue.
- Librarians receive an in-app notification when a Guest submits a request.
- Wishlist views can filter by origin, including Librarian-created, Guest-requested, and individual requester.
- The Guest role must remain intentionally limited. Requesting media must not grant catalog-administration or filesystem permissions.

### Wishlist workflow

- Librarians can add movies or TV series directly without a Guest request.
- Entries support freeform notes and reference URLs so Librarians can keep research pages, availability notes, edition preferences, or other acquisition context with the item.
- Detecting a Wishlist item in the InfoMancer Library must not remove it automatically.
- Instead, InfoMancer should flag the entry as apparently available and surface an actionable notification to the Librarian.
- Optional availability checks can later include configured Plex and Jellyfin servers in addition to the InfoMancer catalog.
- The Librarian decides when an item is fulfilled, dismissed, or retained for another edition/version.
- For Guest requests, a Librarian can send an in-app availability/fulfilled notification back to the original requester.

Suggested states: `wanted`, `acquiring`, `available-detected`, `fulfilled`, `dismissed`. Detection should be advisory rather than an automatic state transition to fulfilled.

## JDownloader 2 integration

Start with an opt-in, read-only download-status adapter rather than trying to make InfoMancer a download client.

- Connect to a JDownloader instance through the supported My.JDownloader API, with a local/direct adapter considered separately for advanced installations.
- Let a Librarian associate a Wishlist/request item with a JDownloader package identifier.
- Surface package name, bytes downloaded/total, progress percentage, current speed, ETA, running/finished state, and JDownloader status.
- Use explicit package/link identifiers for durable association instead of fuzzy filename matching alone.
- Show this progress in the Wishlist and optionally the existing InfoMancer background/task surface.
- Do not expose JDownloader credentials or arbitrary download URLs to Guest users.

Later, an explicitly opt-in handoff could send user-supplied links to JDownloader or generate a Folder Watch `.crawljob`. That should be a separate capability from read-only monitoring and should preserve clear user control over what is submitted.

## Pre-release Feature Requests

Dev, Alpha, and Beta builds should expose a Feature Requests section for structured product feedback.

- Visible only on development/pre-release channels, not stable releases by default.
- Accept title, description, use case, optional screenshots/context, and app/build diagnostics that the user can review before submission.
- Keep feature requests separate from bug reports and crash/error diagnostics.
- Support local draft/history even if no remote submission endpoint is configured.
- Future hosted submission should disclose exactly what is sent and require an explicit action by the user.

## Developer announcements redesign

The base application should not ship historical announcements as active inbox items.

Future official announcements should be versioned, remotely published developer messages for meaningful releases, feature launches, migrations, or urgent notices. They should be cryptographically/verifiably sourced or delivered through a trusted InfoMancer release service, remain clearly distinct from locally authored Librarian announcements, and avoid re-showing irrelevant historical messages on fresh installs.
