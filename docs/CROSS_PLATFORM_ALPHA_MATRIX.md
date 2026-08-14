# InfoMancer cross-platform alpha matrix

Use this matrix for every alpha candidate. Record the operating system, Docker
Desktop or Docker Engine version, filesystem type, and result in the release
issue or pull request.

| Platform | Install and first run | Add Movie source | Add TV source | Scan | Restart | Backup and restore |
| --- | --- | --- | --- | --- | --- | --- |
| Windows 11 + Docker Desktop | Pending | Pending | Pending | Pending | Pending | Pending |
| macOS 14+ + Docker Desktop | Pending | Pending | Pending | Pending | Pending | Pending |
| Ubuntu 24.04 LTS + Docker Engine | Pending | Pending | Pending | Pending | Pending | Pending |
| Debian 12 + Docker Engine | Pending | Pending | Pending | Pending | Pending | Pending |

## Filesystem cases

- Paths containing spaces, punctuation, and non-ASCII characters.
- Read-only media mounts and read/write mounts.
- Windows drive-letter paths mapped into Docker.
- macOS external volumes mapped into Docker.
- Linux local, SMB, and NFS mounts mapped into Docker.
- Large catalogs and empty first-run installations.

## Pass criteria

1. Setup and sign-in complete without editing application source files.
2. The source browser exposes only configured container paths.
3. Movie and TV scans catalog files without modifying them.
4. Preview actions remain previews until explicitly confirmed.
5. A database backup verifies successfully and restores into a disposable test
   installation.
6. The diagnostic bundle downloads without passwords, sessions, API keys,
   encryption keys, or the media database.

Do not mark a platform supported until another person has completed the matrix
on that platform. A passing Windows test does not imply macOS or Linux support.
