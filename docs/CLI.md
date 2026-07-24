# InfoMancer command line

InfoMancer includes a command-line interface for headless servers, scheduled
maintenance, backups, exports, and troubleshooting. The web application does
not need to be open in a browser.

## Running commands

With Docker Compose:

```bash
docker compose -f compose.yaml -f compose.media.yaml exec infomancer python -m app.cli status
```

Add every Compose overlay used by the installation to that command. For
example, an installation using the Cloudflare overlay would include
`-f compose.cloudflare.yaml` before `exec`.

For a native Python installation:

```powershell
# Windows
.\.venv\Scripts\python.exe -m app.cli status
```

```bash
# macOS or Linux
./.venv/bin/python -m app.cli status
```

Run `python -m app.cli --help` or append `--help` to any command for the exact
options supported by that version.

## Commands

### Status

```bash
python -m app.cli status
```

Shows the database location, catalog totals, enabled sources, file counts, and
last scan times. Source IDs shown here can be passed to `scan --source`.

### Diagnostics

```bash
python -m app.cli doctor
```

Checks SQLite integrity, configured storage paths, FFprobe availability, and
TheTVDB configuration. Warnings explain which features remain available and
what needs attention. No media or catalog records are changed.

### Scan

```bash
python -m app.cli scan --source 2
python -m app.cli scan --source "Living Room Movies"
python -m app.cli scan --all
```

A scan adds new media, updates changed files, and removes catalog records for
files no longer present. It does not rename, move, or delete media files. The
command asks for confirmation; use `--yes` only for trusted unattended jobs.

Do not start overlapping scans from the CLI and web interface. Let the current
scan finish before starting another.

### Inspect media

```bash
python -m app.cli inspect
python -m app.cli inspect --title 42
python -m app.cli inspect --title "Example Movie" --all
```

By default, this runs FFprobe only for files missing technical media data or
whose previous inspection failed. `--all` reinspects matching files. Use
`--yes` for unattended execution.

### Export

```bash
python -m app.cli export --format csv --output ./exports/
python -m app.cli export --format json --output library.json
python -m app.cli export --format xml --user librarian
```

CSV, JSON, and XML exports contain titles, provider IDs, paths, technical media
information, ratings, and genres. Pass `--user USERNAME` to include that
person's favorites, personal ratings, custom order, and tags. Without it, the
export contains shared catalog data and no private per-user selections.

### Logs

```bash
python -m app.cli logs
python -m app.cli logs --level error --limit 500
python -m app.cli logs --category scan --follow
python -m app.cli logs --export ./exports/
```

`--follow` prints new events until `Ctrl+C` is pressed. Filters can be combined
with viewing and export.

### Backup

```bash
python -m app.cli backup --output ./backups/
```

Creates a consistent SQLite backup using SQLite's live-backup interface. The
web application may remain running. Media files are not included because they
remain in their existing storage locations.

### Optimize

```bash
python -m app.cli optimize
```

Refreshes SQLite indexes and query statistics and checkpoints the write-ahead
log. It asks for confirmation unless `--yes` is supplied.

### Recover a Librarian

```bash
python -m app.cli reset-librarian USERNAME
```

Prompts for a temporary password without putting it in shell history, revokes
the Librarian's existing sessions, and requires a password change at the next
sign-in.

## Scheduling

Commands that support `--yes` can be run through cron, systemd timers, Windows
Task Scheduler, or another automation system. Capture both standard output and
standard error in the scheduler's log. Start with `doctor`, `backup`, and
`status`; schedule catalog scans only after confirming that storage is always
mounted before the job begins.

If network storage is unavailable, InfoMancer stops that source scan and
reports the inaccessible path instead of treating the entire source as empty.

