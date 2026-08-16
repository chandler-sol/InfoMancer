# InfoMancer Windows Desktop

This directory contains the Tauri v2 Windows shell for InfoMancer 0.8.0-alpha.1.
It can launch a bundled Python core on loopback or connect to an existing
InfoMancer server.

## Security boundary

Only the bundled launcher document has Tauri IPC capability. After the window
navigates to the local or remote InfoMancer HTTP app, that web content is not
granted native shell or updater commands.

## Local data

The standalone core uses Tauri's application-data directory for
`cloud.arsenik.infomancer`. The bundled Python sidecar keeps local auth, Host/Origin/CSRF
protections, loopback-only binding, and disabled proxy-header trust.

## Uninstall contract

A normal interactive uninstall is zero-residue for InfoMancer-owned state. The
confirmation checkbox is mandatory. If a local database exists, the uninstaller
offers one last chance to save a verified `.infomancer-backup` to a location the
user chooses outside InfoMancer data. If backup creation or verification fails,
uninstall stops unless the user explicitly chooses to continue without it.

Updater-driven replacement is not treated as uninstall and therefore preserves
application data. Silent uninstall skips the recovery prompt and is intended for
CI or explicit unattended deployment.

## Updates without InfoMancer-hosted infrastructure

Windows binaries and updater manifests are hosted as GitHub Release assets.
Alpha builds read a rolling manifest at the `desktop-alpha` GitHub release. The
manifest points to versioned release assets. Tauri verifies every update with its
mandatory updater signature before installation.

The updater signing private key is never stored in this repository. Release CI
expects:

- repository variable `TAURI_UPDATER_PUBLIC_KEY`
- Actions secret `TAURI_SIGNING_PRIVATE_KEY`
- optional Actions secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

Generate the key pair once from this directory with:

```powershell
npm run tauri signer generate -- -w $HOME\.tauri\infomancer.key
```

Back up the private key somewhere independent of GitHub. Losing it means already
installed clients cannot trust future updater artifacts.

## Build locally

```powershell
python -m pip install -r ..\requirements.txt
python -m pip install pyinstaller==6.21.0
python -m PyInstaller --noconfirm --clean --onefile --name infomancer-core --add-data "..\app\templates;app/templates" --add-data "..\app\static;app/static" sidecar.py
$triple = (rustc --print host-tuple).Trim()
New-Item -ItemType Directory -Force src-tauri\binaries | Out-Null
Copy-Item ..\dist\infomancer-core.exe "src-tauri\binaries\infomancer-core-$triple.exe"
npm ci
npm run icon
npm run build
```

GitHub's `Windows Desktop` workflow performs the supported reproducible preview
build and also smoke-tests the sidecar recovery mode plus silent zero-residue
uninstall.
