# Backup and Restore

InfoMancer uses `.infomancer-backup` files for portable catalog backups.

A recovery package is meant to preserve InfoMancer's catalog and application state. It does **not** copy your Movie or TV files into the backup.

# When to make a backup

Create a fresh backup before:

- installing a beta update you care about
- doing a clean reinstall
- moving an installation to another computer
- making major storage or source changes
- testing filesystem-changing features on an important catalog

# What a recovery package contains

A portable recovery package can contain:

- the InfoMancer catalog and account state
- library organization such as Collections, ratings, tags, and other saved state
- InfoMancer-managed collection artwork
- a manifest identifying the InfoMancer version that created the package
- size and SHA-256 integrity information for the restorable files

It does **not** contain:

- Movie or TV media files
- TVDB or other provider credentials
- provider-secret encryption keys
- Server `.env` files
- application binaries
- caches

Treat the backup as private. The catalog can still contain account information, source paths, filenames, ratings, tags, and other library details.

# Create a backup

1. Open **Settings > System**.
2. Create and download a portable recovery package.
3. Keep the `.infomancer-backup` file somewhere outside InfoMancer's application-data directory.

If you are making the backup before uninstalling or reinstalling, keep it somewhere the uninstall process will not remove.

InfoMancer verifies the package before presenting it as complete.

# Restore a backup

For a clean reinstall or move:

1. Install InfoMancer normally.
2. Make sure the computer or Server can reach the same media storage. Reconnect drives, network shares, or Server media mappings before restoring when possible.
3. Complete the temporary first-run setup if InfoMancer requires it so you can reach Librarian Settings.
4. Open **Settings > Recovery**.
5. Select the `.infomancer-backup` file.
6. Choose **Verify package & preview restore**.
7. Review the backup version, creation time, database size, artwork count, and exclusions.
8. Type `RESTORE` when you are ready to commit the restore.
9. Let InfoMancer restart.
10. Sign in with an account from the restored catalog.
11. Re-enter TVDB or other provider credentials.
12. Open Sources and confirm every source points to the intended storage.
13. Run a scan and review the results before resuming filesystem-changing work.

The temporary first-run account is replaced when the restored database is committed.

# If your media paths changed

A backup remembers the source paths from the original installation.

If the new computer or Server uses different drive letters, mount points, or network-share paths, make the corresponding storage available before running file-changing actions.

For Docker Server installs, restoring the same host media folders to compatible `/media/...` mappings is the simplest path.

Do not weaken InfoMancer's trusted browse-root restrictions just to make an old backup pass validation. If source paths need to change, update them through supported InfoMancer source-management workflows after the restore environment is safe.

# What happens during restore

Before InfoMancer replaces the live catalog, it verifies the recovery package and staged database.

Immediately before commit, InfoMancer creates a fresh safety package of the current installation. The database and InfoMancer-managed collection artwork are then restored as one rollback-protected operation.

If verification fails, the live installation is not changed.

If commit fails, InfoMancer attempts to roll back rather than leave the database and artwork in a mixed state.

# If a restore reports a serious failure

If InfoMancer says automatic rollback was incomplete:

1. Stop using that installation.
2. Do not run filesystem-changing operations.
3. Preserve the entire InfoMancer application-data directory.
4. Preserve the pre-restore safety package identified by the error, if one was created.

Use that preserved state for controlled recovery instead of repeatedly retrying changes against a partially restored installation.

# Server deployment backup

A portable `.infomancer-backup` protects the InfoMancer catalog, but a Server deployment also has local configuration outside that package.

For a complete Server deployment backup, protect these together:

- `data/`
- `.env`
- `compose.media.yaml`

Your actual Movie and TV files remain separate from InfoMancer backups.
