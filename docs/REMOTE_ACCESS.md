# Remote Access

You do not need remote-access software to use InfoMancer around your home.

| Where you are using InfoMancer | Recommended setup |
| --- | --- |
| On the same trusted network as the Server | Nothing extra. Use the Server's normal LAN address. |
| Away from home and you already use a private VPN | Connect the VPN, then use InfoMancer as if you were at home. |
| Away from home and you want a public HTTPS hostname | Use an authenticated reverse proxy. The Cloudflare option is documented below. |

**Never port-forward port 8787 directly to the public Internet.**

# On your home network

A normal InfoMancer Server installation listens on the trusted local network. Other devices on that network can use:

`http://SERVER-IP:8787`

For example:

`http://192.168.1.50:8787`

The guided Server setup helper prints the address when it can detect the Server's LAN address.

If InfoMancer works on the Server computer but another computer cannot connect, check the Server operating system's firewall and allow TCP port `8787` on the trusted/private network.

# Away from home

A private VPN is usually the simplest option. Connect to the home network through the VPN, then use the same InfoMancer Server address or private hostname you normally use at home.

If you prefer a public HTTPS hostname, put an authenticated reverse proxy in front of InfoMancer and keep the InfoMancer origin private. Do not expose the application directly just because the proxy provides HTTPS.

The rest of this guide covers the supported Cloudflare Access and Tunnel approach.

# Advanced: Cloudflare Access and Tunnel

A Cloudflare Tunnel makes an outbound connection from the InfoMancer host, so the router does not need a port-forward or public inbound firewall rule.

Use a hostname you control, such as `infomancer.example.com`, throughout these instructions.

## Make the Cloudflare origin host-only

Before using Cloudflare Tunnel, change this line in the Server `.env` file:

```dotenv
INFOMANCER_BIND_ADDRESS=127.0.0.1
```

Then restart InfoMancer:

```bash
docker compose -f compose.yaml -f compose.media.yaml down
docker compose -f compose.yaml -f compose.media.yaml up -d
```

This keeps port 8787 reachable from the Server host itself while preventing direct LAN or public connections to the host port. The Cloudflare connector can still reach the InfoMancer service through the Docker network when using the dedicated connector option below.

If you want to keep direct LAN access as well as a remote-access path, a private VPN is usually the simpler choice.

## Protect the hostname first

Keep a Cloudflare Access **self-hosted application** on the entire hostname:

1. In Cloudflare One, open **Access controls > Applications**.
2. Add a self-hosted web application for `infomancer.example.com`.
3. Add an **Allow** policy containing only the exact users or identity-provider group that should reach InfoMancer.
4. Do not add an Everyone, all-email, or Bypass rule.
5. Choose a reasonable session duration and require MFA in the identity provider when possible.

Test the policy in a private browser window. Cloudflare's sign-in page must appear before InfoMancer.

This outer policy can protect InfoMancer while the application continues using its normal local accounts. For that layout, tell InfoMancer its canonical HTTPS address and explicitly trust Cloudflare proxy metadata:

```dotenv
INFOMANCER_PUBLIC_URL=https://infomancer.example.com
INFOMANCER_TRUSTED_HOSTS=infomancer.example.com
INFOMANCER_TRUST_CLOUDFLARE_PROXY=true
```

Only enable `INFOMANCER_TRUST_CLOUDFLARE_PROXY` while the origin remains private. It lets local-account installations use the real Cloudflare client IP and HTTPS scheme without trusting arbitrary forwarded headers from direct clients.

To make Cloudflare the application sign-in authority too, set:

```dotenv
INFOMANCER_AUTH_MODE=cloudflare
CF_ACCESS_TEAM_DOMAIN=https://your-team.cloudflareaccess.com
CF_ACCESS_AUD=the-application-audience-tag-from-cloudflare
```

Local accounts remain the normal choice when you do not need Cloudflare to be the application sign-in authority.

The first verified visitor must also enter the one-time setup code to complete Librarian setup. Afterward, a Librarian must create each later account with the exact email address Cloudflare asserts.

Restart InfoMancer after changing authentication environment values.

## Option A: reuse an existing connector

When `cloudflared` already runs directly on the InfoMancer host, add a published application route to that tunnel:

- Public hostname: `infomancer.example.com`
- Service type: HTTP
- Service URL: `http://localhost:8787`

Cloudflare creates the proxied DNS route. No additional InfoMancer Compose file is required.

## Option B: run a dedicated connector

Create a remotely managed tunnel in **Cloudflare Dashboard > Networking > Tunnels** and copy its token. Treat the token as a password.

From the InfoMancer Server folder:

```bash
cp .env.cloudflare.example .env.cloudflare
chmod 600 .env.cloudflare
```

Place the token after `TUNNEL_TOKEN=` in `.env.cloudflare`. In the tunnel dashboard, add:

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

## Verify it before relying on it

1. Confirm `http://127.0.0.1:8787` responds on the Server host.
2. Open the public hostname in a private browser window.
3. Confirm an unauthorized identity is denied by Access.
4. Confirm HTTPS and the expected InfoMancer login.
5. Preview, but do not apply, a rename as a final functional check.

## Remove Cloudflare remote access

Delete or disable the published route. If you use the dedicated connector, also run:

```bash
docker compose -f compose.yaml -f compose.media.yaml -f compose.cloudflare.yaml \
  stop cloudflared
```

Never commit `.env.cloudflare`, the tunnel token, `.env`, TVDB credentials, setup codes, or the SQLite database.
