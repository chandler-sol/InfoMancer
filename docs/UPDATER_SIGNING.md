# Updater Signing and Channel Activation

InfoMancer's desktop updater will not publish installable Dev, Beta, or Standard channel artifacts unless Tauri updater signing is configured in GitHub Actions.

The qualification pipeline is intentionally allowed to stay green when signing is unavailable. In that state the source commit is still qualified, but no rolling install channel is advanced.

## Required GitHub Actions configuration

Configure these values in the repository's Actions settings:

- Repository variable `TAURI_UPDATER_PUBLIC_KEY`
- Actions secret `TAURI_SIGNING_PRIVATE_KEY`
- Actions secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` when the private key is password protected

The public key is compiled into signed desktop builds through `INFOMANCER_UPDATER_PUBLIC_KEY`. The private key is only supplied to the packaging job through GitHub Actions secrets and must never be committed to the repository.

Keep the private signing key and its password outside the InfoMancer repository. Losing the private key means existing installations that trust its public key cannot verify packages signed by a replacement key without a deliberate trust migration.

## Dev activation check

After signing is configured, push a normal change to `testing/0.9-alpha` or re-run qualification from a new commit.

The repository owner provisioned the signing variable and secrets on 2026-09-13. The next qualifying push is the activation proof: the signed packaging steps must execute rather than skip, and the Dev rolling pointers must advance only after the complete qualification gates pass.

The canonical `Tests` workflow must complete all of these before Dev can advance:

1. Windows Python tests
2. macOS Python tests
3. Linux Python tests
4. security and dependency audit
5. browser acceptance
6. qualified candidate manifest generation
7. signed Windows Dev packaging

The packaging job checks out the exact commit that passed qualification, stamps the build workspace as `0.9.0-dev.<run>`, signs the updater package, publishes an immutable Dev release, then advances:

- `desktop-dev/latest.json`
- `update-channels/dev.json`

A failed, cancelled, or incomplete qualification run cannot advance either pointer.

## Beta and Standard promotion

Qualified builds are promoted with `.github/workflows/promote-update-channel.yml` once that workflow is present on the repository's default branch.

Promotion deliberately does not choose a new source commit. It:

1. downloads the currently qualified source channel manifest
2. reads its immutable build id and qualified commit SHA
3. checks out that exact commit
4. stamps only the target release version in the packaging workspace
5. rebuilds/signs version-bearing Tauri packaging metadata as required
6. preserves the source build id, source commit, qualification run, qualification gates, qualification timestamp, and database schema contract
7. records promotion provenance in the target channel manifest
8. advances the target rolling pointer only after signing and manifest validation succeed

Allowed directions are:

- Dev to Beta
- Dev to Standard
- Beta to Standard

Promotion to a less stable channel is rejected.

Version-bearing signed artifacts are never silently copied to a different release version. A promotion must supply newly verified target-version artifact metadata even though the application source commit and qualification identity stay unchanged.

## Server trust remains separate

The channel manifest is not a replacement for server release signing.

A Server update remains installable only when the qualified channel manifest contains a server artifact with a trusted release tag. The host updater independently verifies that tag's GPG signature and also verifies that the signed tag resolves to the same commit SHA recorded by the qualified manifest.

This means compromise of a rolling channel manifest alone cannot authorize an arbitrary Server checkout.

## 0C final operational proof

Cycle 0C should not be declared fully activated until all of the following have happened at least once with real signed artifacts:

- a qualified Dev build advances the Dev desktop/channel pointers
- that exact qualified build is promoted to Beta or Standard without changing its source commit/build identity
- a schema-compatible version downgrade is exercised through the signed updater/rollback path
- the incompatible/read-only downgrade cases remain blocked

The automated tests exercise the promotion invariants, schema assessment, trusted-tag/commit binding, successful downgrade host path, and rollback path. The final signed-package proof still requires repository signing credentials and published artifacts.
