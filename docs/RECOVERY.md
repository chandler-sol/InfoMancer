# Portable recovery and clean reinstall

InfoMancer 0.8 uses `.infomancer-backup` as its portable recovery format. The same package is
created from Settings and by the native Windows uninstall recovery flow. It is intended to carry
InfoMancer-owned catalog state across a clean reinstall without copying movie or TV media into the
archive.

## What a recovery package contains

A portable package contains:

- a consistent SQLite snapshot of the InfoMancer catalog and account state;
- collection artwork stored by InfoMancer;
- a versioned manifest identifying the InfoMancer version that created the package;
- the size and SHA-256 checksum of every restorable payload.

It deliberately does **not** contain movie or TV media, provider credentials, provider-secret
encryption keys, deployment `.env` files, application binaries, or caches. Provider credentials
must be entered again after recovery.

Treat a recovery package as sensitive even though provider credentials are excluded. The database
can contain user accounts, library organization, source paths, filenames, ratings, tags, and other
installation state.

## Create and verify a package before reinstalling

From **Settings > System**, create and download a portable recovery package. InfoMancer verifies the
package before presenting it as complete. Keep the file somewhere outside the InfoMancer application
data directory and, for an uninstall/reinstall, outside any directory the uninstaller will remove.

For the native Windows application, accept the optional final recovery backup before uninstalling
when you want to preserve the installation. That package uses the same format as the in-app
recovery workflow.

## Clean reinstall and restore

1. Install the new InfoMancer build normally and start it with a new/empty application-data
   directory.
2. Recreate the storage mappings required to reach the existing media. For Docker, mount the same
   media roots at paths compatible with the recovered source definitions. For native Windows,
   reconnect the same drives or UNC shares where practical.
3. Complete temporary first-run setup so you can enter Librarian Settings. This temporary account
   is replaced when the recovered database is committed.
4. Open **Settings > Recovery** and select the `.infomancer-backup` file.
5. Choose **Verify package & preview restore**. InfoMancer verifies archive paths, the manifest,
   every declared size/checksum, and the staged SQLite database before it changes the live
   installation.
6. Review the source InfoMancer version, creation time, database size, artwork count, and exclusions.
7. Type `RESTORE` and submit the final confirmation.
8. Before commit, InfoMancer creates and verifies a fresh portable safety package of the current
   installation. It then restores the database and collection artwork as one rollback-protected
   operation. If commit fails, both are rolled back rather than leaving a mixed installation.
9. InfoMancer restarts. Sign in using an account from the recovered database.
10. Re-enter TVDB/provider credentials and verify provider status. Provider-secret storage is never
    taken from the recovery archive.
11. Open Sources and confirm every recovered source resolves to the intended local disk/share.
    Reconnect or correct deployment mounts before running destructive filesystem operations.
12. Run a scan and inspect Review/Activity before resuming normal filesystem automation.

## Path validation and moved libraries

Recovery validates source/media paths stored in the incoming database against the installation's
configured browse roots before replacing the live database. This prevents a package from silently
introducing arbitrary filesystem locations.

If a clean reinstall intentionally changes mount points, first configure the new installation so the
corresponding trusted browse roots are available. Do not weaken browse-root restrictions merely to
make an old package pass validation. If source paths themselves must change, recover in a controlled
environment, update the source mapping through supported InfoMancer workflows, then create a fresh
portable package.

## Failure behavior

A failed verification never starts a restore. A failure while staging also leaves the live database
and artwork untouched. Once commit begins, InfoMancer keeps a rollback database snapshot and the old
collection-art directory until the incoming database validates in its live location. A successful
restore keeps the separately created pre-restore safety package in `recovery-packages` so there is a
known-good return point.

If InfoMancer reports that automatic rollback itself was incomplete, stop using the installation and
preserve the application-data directory. The error identifies the pre-restore safety package when it
was successfully created; use that package for controlled recovery rather than attempting more
filesystem operations.

## 0.8 release qualification

The restore implementation is present in 0.8 alpha, but public-release qualification still requires
the clean reinstall matrix in `docs/QA_0_8.md`: supported Windows/Docker platforms, real network
shares, interrupted restore fault injection, old supported database versions, and provider
re-authentication after restore. Those are release gates, not implied guarantees of an alpha build.
