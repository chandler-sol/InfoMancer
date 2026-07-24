# Remote access with Cloudflare

InfoMancer should remain private at its origin. `compose.yaml` binds port 8787
to loopback only. A Cloudflare Tunnel makes an outbound connection, so the
router does not need a port-forward or public inbound firewall rule.

Use a hostname you control, such as `infomancer.example.com`, throughout these
instructions.

## Protect the hostname first

Keep a Cloudflare Access **self-hosted application** on the entire hostname:

1. In Cloudflare One, open **Access controls > Applications**.
2. Add a self-hosted web application for `infomancer.example.com`.
3. Add an **Allow** policy containing only the exact users or identity-provider
   group that should reach InfoMancer.
4. Do not add an Everyone, all-email, or Bypass rule.
5. Choose a reasonable session duration and require MFA in the identity
   provider when possible.

Test the policy in a private browser window. Cloudflare's sign-in page must
appear before InfoMancer.

This outer policy can protect InfoMancer while the application continues using
local accounts. To make Cloudflare the application sign-in authority too, set:

```dotenv
INFOMANCER_AUTH_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=https://your-team.cloudflareaccess.com
CF_ACCESS_AUD=the-application-audience-tag-from-cloudflare
```

The first verified visitor completes Librarian setup. Afterward, a Librarian
must create each later account with the exact email address Cloudflare asserts.
Restart InfoMancer after changing authentication environment values.

## Option A: reuse an existing connector

When `cloudflared` already runs on the InfoMancer host, add a published
application route to that tunnel:

- Public hostname: `infomancer.example.com`
- Service type: HTTP
- Service URL: `http://localhost:8787`

Cloudflare creates the proxied DNS route. Keep the tunnel's final catch-all
rule. No InfoMancer Compose change is required.

## Option B: run a dedicated connector

Create a remotely managed tunnel in **Cloudflare Dashboard > Networking >
Tunnels** and copy its token. Treat the token as a password.

From the InfoMancer folder:

```bash
cp .env.cloudflare.example .env.cloudflare
chmod 600 .env.cloudflare
```

Place the token after `TUNNEL_TOKEN=` in `.env.cloudflare`. In the tunnel
dashboard, add:

- Public hostname: `infomancer.example.com`
- Service type: HTTP
- Service URL: `http://infomancer:8787`

Start the application, its media mapping, and the connector:

```bash
docker compose -f compose.yaml -f compose.media.yaml -f compose.cloudflare.yaml \
  up -d --build
```

Check both services:

```bash
docker compose -f compose.yaml -f compose.media.yaml -f compose.cloudflare.yaml ps
docker compose -f compose.yaml -f compose.media.yaml -f compose.cloudflare.yaml \
  logs --tail=100 cloudflared
```

## Verification and rollback

1. Confirm `http://127.0.0.1:8787` still responds on the host.
2. Open the public hostname in a private browser window.
3. Confirm an unauthorized identity is denied by Access.
4. Confirm HTTPS and the expected InfoMancer login.
5. Preview, but do not apply, a rename as a final functional check.

To remove remote access immediately, delete or disable the published route. If
using the dedicated connector, also run:

```bash
docker compose -f compose.yaml -f compose.media.yaml -f compose.cloudflare.yaml \
  stop cloudflared
```

Never commit `.env.cloudflare`, the tunnel token, `.env`, TVDB credentials, or
the SQLite database.
