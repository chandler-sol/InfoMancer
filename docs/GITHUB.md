# Moving InfoMancer to GitHub

## Recommended first step: a private repository

A private GitHub repository is the best fit while filesystem operations and
remote access are still being hardened. It provides history, off-machine code
backup, issues, and automated tests without publishing the project. Private
does not make committed secrets safe: environment files, tunnel tokens, API
keys, and the SQLite database must remain untracked.

Before the first push:

1. Install Git for Windows and optionally the GitHub CLI (`gh`).
2. Run the test suite.
3. Review `git status` and the staged diff carefully.
4. Verify that `.env`, `.env.cloudflare`, `tvdb.env`, and `data/` are absent.
5. Verify that `compose.atlas.yaml`, `dist/`, and generated archives are absent.

If this folder is not already a Git repository:

```powershell
git init
git add .
git status
git diff --cached
git commit -m "Initial InfoMancer release"
```

With GitHub CLI:

```powershell
gh auth login
gh repo create infomancer --private --source=. --remote=origin --push
```

## Create an alpha package

The release builder uses an explicit allowlist. It does not include local
environment files, databases, media, `compose.atlas.yaml`, Cloudflare
credentials, or generated deployment archives.

```powershell
.\.venv\Scripts\python.exe scripts\build_release.py
```

On macOS or Linux:

```bash
python3 scripts/build_release.py
```

Upload both files created in `dist/` to the GitHub release:

- `InfoMancer-VERSION.zip`
- `SHA256SUMS.txt`

After the initial commit and push, create a private prerelease with GitHub CLI:

```powershell
gh release create v0.3.0-alpha.1 `
  .\dist\InfoMancer-0.3.0-alpha.1.zip `
  .\dist\SHA256SUMS.txt `
  --prerelease `
  --title "InfoMancer 0.3.0 Alpha 1" `
  --generate-notes
```

Only friends who have access to the private repository can open or download
its private releases.

Without GitHub CLI, create an empty private repository on GitHub (do not add a
README, license, or `.gitignore` there), then use the `git remote add` and
`git push` commands GitHub displays.

## Later options

- **Keep it private:** simplest for a personal administration tool.
- **Publish it:** perform a privacy/security review, choose a license, replace
  home-specific examples, and create contribution/security policies first.
- **Deploy from GitHub:** add a self-hosted runner or a server pull/deploy
  script only after repository access and secret handling are settled. Do not
  expose Docker control or media credentials to pull requests.

No license is added yet. If the repository becomes public, common choices are
MIT (permissive and short), Apache-2.0 (permissive with an explicit patent
grant), or GPL-3.0 (derivatives distributed under the same license).

The included GitHub Actions workflow installs dependencies, runs the unit
tests, and compiles the application on pushes and pull requests. It does not
need or receive TVDB, IMDb, Cloudflare, or filesystem secrets.
