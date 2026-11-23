# Sign-in with PocketID

This fork replaces Spoolman's open access with OIDC sign-in (built and tested against
[PocketID](https://pocket-id.org)), a separate account per person, and personal printer keys.

## 1. PocketID

1. Create an OIDC client named `Spoolman`.
2. Callback URL: exactly `<SPOOLMAN_PUBLIC_URL><SPOOLMAN_BASE_PATH>/auth/callback`,
   for example `https://spoolman.example.com/auth/callback`
   (or `https://example.com/spoolman/auth/callback` with a base path).
3. Leave PKCE on. Spoolman is a confidential client: it sends PKCE (S256) and the client secret.
4. Create a group `spoolman_admins` and add yourself.
5. Restrict the client to the groups allowed to use Spoolman.
6. Copy the client ID and secret.

Spoolman requests the scopes `openid profile email groups`.

## 2. Environment

| Variable | Required | Notes |
| --- | --- | --- |
| `SPOOLMAN_OIDC_ISSUER` | yes | PocketID base URL. Discovery is retried for 60 s, so PocketID may start later. |
| `SPOOLMAN_OIDC_CLIENT_ID` | yes | |
| `SPOOLMAN_OIDC_CLIENT_SECRET` or `..._FILE` | yes | `_FILE` reads the secret from a file. |
| `SPOOLMAN_PUBLIC_URL` | yes | Address people use, no trailing path. Behind a TLS proxy set it to `https://...`. |
| `SPOOLMAN_SESSION_SECRET` or `..._FILE` | yes | At least 32 bytes. |
| `SPOOLMAN_OIDC_ADMIN_GROUP` | no | Default `spoolman_admins`. |
| `SPOOLMAN_BASE_PATH` | no | Host under a sub-path, e.g. `/spoolman`. |
| `SPOOLMAN_METRICS_TOKEN` or `..._FILE` | no | At least 32 characters. Enables `/metrics`, see below. |

The app refuses to start if a required variable is missing. `SPOOLMAN_LEGACY_CLIENT` is ignored
(with a warning); the legacy client is gone. The session cookie is `Secure` unless
`SPOOLMAN_PUBLIC_URL` starts with `http://`.

See [docker-compose.example.yml](docker-compose.example.yml) for Docker Compose and Unraid.
Image: `ghcr.io/jfow91/spoolman-sso:latest` (also `:sha-<short>`).

## 3. First sign-in and existing data

Everyone signs in through PocketID and gets their own account. Data from before the upgrade
belongs to a placeholder until an admin clicks **Claim existing data** (shown once after sign-in).
If a setting or tag clashes with one the admin already has, the admin's own wins.

### Upgrading from Spoolman

- Back up the database first. Downgrading is not clean: it fails if two accounts share a setting
  key or tag UID, and otherwise merges every account's rows with no owner.
- Every existing integration (Moonraker, Home Assistant, OctoPrint, NFC/ESP readers posting to
  `/tag/scan`) gets 401 until it is given a key URL or header (see Printer keys).
- Claim right after the first admin sign-in, before configuring anything: your own settings,
  custom fields and locations win over the old data on a clash.
- A database already migrated by an upstream Spoolman release newer than this fork's base
  (2026-11-10) will not start. The fork must be the next version you run.

An admin is a member of the admin group at last login. Admins can list, disable, enable and
revoke access for users, and trigger backups (`POST /backup` is admin only). Admins cannot see
other people's data. To remove someone, also Disable them on the Users page (or Revoke access);
PocketID changes apply at their next sign-in, and sessions last 30 days while in use. Enabling a
user again does not restore their keys; they must create new ones.

`SPOOLMAN_CORS_ORIGIN=*` or debug mode turns off the origin guard, which matters for sign-in
cookies on homelab sibling subdomains. Leave both off when using sign-in.

## 4. Printer keys

Printers and bridges (Moonraker, OctoPrint, ...) cannot sign in, so each person creates keys on
the **Printer keys** page. A key is shown once, so copy it then. Level **Edit** can change data;
**View** is read-only.

Use a key in any of these ways:

- URL prefix: `https://<host><base>/k/<key>/` (paste this as the Spoolman URL in your bridge)
- `Authorization: Bearer <key>`
- `X-Api-Key: <key>`

Keys reach only `/api/v1` and websockets, never pages or the key, admin and claim endpoints.
Spoolman redacts keys in its own logs (`/k/<redacted>`), but a reverse proxy may log the URL,
so prefer the headers where the client supports them.

## 5. Metrics

`/metrics` is served only with `Authorization: Bearer <SPOOLMAN_METRICS_TOKEN>` and returns 404
when no token is set. Sessions and printer keys cannot read it.

```yaml
scrape_configs:
  - job_name: spoolman
    metrics_path: /metrics        # prefix with SPOOLMAN_BASE_PATH if set
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/spoolman_token   # or: credentials: <token>
    static_configs:
      - targets: ["spoolman:8000"]
```

If Prometheus is not on the same Docker network, use `<unraid-ip>:7912` as the target.

Series now carry an `owner` label (the display-name slug). Adding a label starts new series, so
existing Grafana panels lose continuity at the upgrade.
