# Manual InfoMancer Server Setup

This page is for advanced installs, unusual storage layouts, troubleshooting, or people who prefer to manage Docker files by hand.

**Most users should use the guided setup in [Installation](INSTALLATION.md) instead.**

# 1. Create the local config files

From the extracted InfoMancer Server folder, copy `.env.example` to `.env` and copy the media template for your operating system to `compose.media.yaml`.

## Windows PowerShell

```powershell
Copy-Item .env.example .env
Copy-Item deploy\windows.compose.yaml.example compose.media.yaml
```

## macOS

```bash
cp .env.example .env
cp deploy/macos.compose.yaml.example compose.media.yaml
```

## Linux

```bash
cp .env.example .env
cp deploy/linux.compose.yaml.example compose.media.yaml
```

# 2. Map your media folders

Open `compose.media.yaml`.

**Only change `source:` for a normal manual install. Leave each `/media/...` `target:` path alone.**

Linux example:

```yaml
- type: bind
  source: /mnt/media/movies
  target: /media/movies
- type: bind
  source: /mnt/media/tv
  target: /media/tv
```

Windows example:

```yaml
source: D:/Movies
```

macOS example:

```yaml
source: /Volumes/Media/Movies
```

The Server needs read access for scanning. Rename, organize, and Managed Trash features also need write access to the affected media folders.

# 3. Linux ownership

On Linux, set the container user to the current account and create the data directory:

```bash
sed -i "s/^INFOMANCER_UID=.*/INFOMANCER_UID=$(id -u)/" .env
sed -i "s/^INFOMANCER_GID=.*/INFOMANCER_GID=$(id -g)/" .env
mkdir -p data
```

# 4. Start InfoMancer Server

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

Check status:

```bash
docker compose -f compose.yaml -f compose.media.yaml ps
```

The `infomancer` service should become healthy.

# 5. Get the first-run setup code

Open this once on the Server:

`http://127.0.0.1:8787/setup`

Then run:

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=100 infomancer
```

Find the line:

```text
InfoMancer first-run bootstrap token: ...
```

Use that token as the one-time setup code for the first Librarian account.

# 6. Open InfoMancer from another computer

Use:

`http://SERVER-IP:8787`

Example:

`http://192.168.1.50:8787`

If the Server works locally but another computer cannot reach it, check the Server operating system's firewall and allow TCP port `8787` on the trusted/private network.

**Do not port-forward port 8787 to the public Internet.** See [Remote Access](REMOTE_ACCESS.md) for safer remote-access options.

# Common commands

## Show status

```bash
docker compose -f compose.yaml -f compose.media.yaml ps
```

## View recent logs

```bash
docker compose -f compose.yaml -f compose.media.yaml logs --tail=200 infomancer
```

## Restart

```bash
docker compose -f compose.yaml -f compose.media.yaml restart infomancer
```

## Stop

```bash
docker compose -f compose.yaml -f compose.media.yaml down
```

## Rebuild and start

```bash
docker compose -f compose.yaml -f compose.media.yaml up -d --build
```

# Existing setup files

The three important Server items are:

- `.env`
- `compose.media.yaml`
- `data/`

Keep them together when backing up or moving a Server install.

Before sharing logs publicly, remove private filenames, paths, addresses, API keys, bootstrap tokens, and session information.
