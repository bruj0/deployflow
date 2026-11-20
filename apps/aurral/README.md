# aurral

Aurral, the self-hosted music app, as one container on one guest.

Aurral is a single image: a Hono server on Node 26 with SQLite inside, a React
frontend it serves itself, and `yt-dlp`, `ffmpeg` and `cloudflared` baked into
the image. There is no database server to run beside it, so this is the same
shape as `homarr` — one image, one Compose file, one unit.

## What is deployed

| | |
|---|---|
| Guest | `aurral`, CT 127 |
| Image | `ghcr.io/lklynet/aurral@sha256:41008fa4…`, pinned by digest |
| Version | `3.0.0` |
| Install root | `/opt/aurral/app`, beside the inert Python venv |
| Compose project | `/opt/aurral/app/project/docker-compose.yml`, written by this role |
| Environment | `/opt/aurral/app/project/.env`, mode `0600`, root-owned |
| Unit | `aurral.service` |
| Port | 3001 |
| State | `/opt/aurral/state/config` — the database, the settings, the users, the job queue |
| Media | `/opt/aurral/state/media` — the Library, at `/data/downloads/aurral` inside the container |
| Secrets | none in `.env`. The admin API key is generated on the guest, once, and kept |

## Install it

```bash
appctl install aurral
appctl install aurral        # must report changed=0
```

The manifest key is `aurral` and the role directory is `apps/aurral/`. They
match: `appctl install` selects the role with `--tags lxc,<key>`, and a key that
is not a role name selects nothing — the play runs, changes nothing and reports
success.

## Two mounts, not one

This is the difference between Aurral and every other container application in
this repository.

`/config` holds Aurral's own state: the SQLite database, the settings, the
users, the job queue, and every stored integration credential encrypted with a
key Aurral derives. `/data` holds the music.

They must not be the same directory, and `/config` must never be the directory
a media server reads. A server that can read `/config` can read every stored
credential. Both are bind mounts under `app_state_dir`, so reinstalling the
container never destroys either, and neither lives in `/var/lib/docker` where a
backup cannot see it and a rebuilt guest destroys it.

## The media directory is the integration point

Aurral's Library folder defaults to `/data/downloads/aurral` inside the
container, which is `<state>/media/downloads/aurral` on the guest.

**A second service that shares files with Aurral has to see the same host
folder at the same container path.** Aurral writes an M3U file per flow and per
static playlist into `_playlists`, and each file lists tracks by the full path
Aurral saw. A media server that sees the same folder at a different path finds
no tracks, and its playlist import produces an empty playlist. This is why the
`storage.mdx` documentation says the same container path everywhere rather
than using a path mapping.

The directory is currently the guest's own. It is not the Jellyfin library:
Jellyfin's media lives inside CT 100's own rootfs on the `data2` volume, and
nothing mounts it into another guest. Sharing it would mean moving the media to
a volume both guests mount, which is a change to the Jellyfin guest rather than
to this one.

## The admin API key is generated once and never regenerated

`AURRAL_API_KEY` is the admin API credential, and Aurral refuses to start when
it is set and shorter than 32 characters.

A regenerated key does not fail the install — Aurral stores its own API key in
the settings table when the environment variable is absent, so the server keeps
serving. What breaks is every script and every automation that authenticates
with the old key, with the symptom "the API key stopped working" rather than
"the install failed".

So the role reads the existing `.env` before writing it and keeps the value it
finds. The key is generated on the guest rather than kept in `.env` on the
controller, because it has no external issuer and because a key in `.env` is a
key that can be rotated by accident.

## The image is pinned by digest, and the pin is checked

`aurral_image` in `apps/aurral/defaults/main.yml` names a digest, not a tag.
`latest` and `3.0.0` resolved to the same digest on 2026-11-20, which is
convenient and temporary: a tag is a moving name and the digest is the thing.

Two things make the pin real:

- `--pull never` on the Compose invocation, so a later `up` cannot fetch
  whatever the tag names today;
- an assertion that reads `docker image inspect --format '{{ json .RepoDigests }}'`
  and fails unless the pinned digest is in it.

That assertion reads **`RepoDigests`, not `.Id`**, and the distinction matters.
The pinned digest is an OCI *index* digest — one entry covering amd64 and arm64
— and `docker pull` resolves it to the manifest for this platform. `.Id` is
therefore the platform manifest's digest, a different string, and comparing the
two fails on a perfectly correct pull.

## The guest is sized for the workers, not for the server

Aurral runs one main server plus background worker child processes, and each
worker may take a 2 GB JavaScript heap (`AURRAL_WORKER_MAX_HEAP_MB`, default
2048). The image is roughly 1.5 GB.

The role refuses a guest below 4096 MB, 2 cores or 10 GB free, so a guest that
is too small fails at a task boundary instead of being OOM-killed hours later
with a message that names a process rather than the guest. `inventory/group_vars/au/main.yml`
sets `lxc_memory: 4096` and `lxc_rootfs_gb: 24`, which is the floor plus room
for a library.

## The Cloudflare tunnel

`aurral_tunnel_enabled` is `false` by default, so a play without a credential
does not fail on a missing secret. Turn it on in the guest's `group_vars`:

```yaml
# inventory/group_vars/au/main.yml
aurral_tunnel_enabled: true
aurral_tunnel_id: "<the tunnel's id>"
```

The connector's token is a secret:

```bash
appctl secrets set aurral_tunnel_token '<the tunnel token>'
```

`appctl install` refuses to run while it is absent, rather than deploying a
connector that starts, fails to authenticate and retries forever while the
hostname stays dead.

**The tunnel is locally managed.** cloudflared reads its ingress from the file
the role writes, and the edge does not push a configuration over it. That
matters: a **remotely managed** tunnel overwrites the local file within a
second of the connector starting, and the hostname then goes wherever the edge
says rather than where the file says. Measured, not assumed.

**The origin is the guest's own loopback** — `http://127.0.0.1:3001` — because
the connector runs on this guest. A DHCP lease can move, and an origin naming
the guest's address breaks the moment it does. Loopback cannot.

**The binary is pinned and fetched onto the guest**, by version and by the
sha256 the release publishes, rather than taken from the Aurral image. The
image's copy of `cloudflared` exists for Aurral's own file sharing, and a
connector that reports its own version is a connector whose version can be
asserted.

**The token is delivered through an EnvironmentFile**, not through argv. A token
on the command line is visible in `ps` to every process on the guest for the
life of the connector.

### Creating the tunnel is an operator act

The role deploys a connector; it does not create the tunnel, because creating
one needs the Cloudflare API with the **Cloudflare Tunnel** permission, or an
origin certificate (`cert.pem`) from `cloudflared tunnel login`. A token with
only **DNS edit** can create the CNAME and nothing else.

A token that can *mint* other tokens is enough, and that is the cheapest way
to get the scope without widening the one you already have:

```bash
# Mint a temporary token with exactly the two permissions the role needs.
curl -s -X POST \
  -H "Authorization: Bearer $CLOUDFLARE_TOKEN_CREATOR" \
  -H "Content-Type: application/json" \
  --data '{
    "name": "deployflow-aurral-tunnel",
    "status": "active",
    "policies": [{
      "effect": "allow",
      "permission_groups": [
        {"id": "efea2ab8357b47888938f101ae5e053f", "name": "Cloudflare Tunnel Read"},
        {"id": "c07321b023e944ff818fec44d8203567", "name": "Cloudflare Tunnel Write"},
        {"id": "4755a26eedb94da69e1066d98aa820be", "name": "DNS Write"}
      ],
      "resources": {
        "com.cloudflare.api.account.<ACCOUNT_ID>": "*",
        "com.cloudflare.api.account.zone.<ZONE_ID>": "*"
      }
    }]
  }' \
  "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/tokens"
```

Then create the tunnel, set its ingress, point the DNS at it, and fetch the
**connector token** — which is not the API token, and is the one the
`aurral_tunnel_token` secret holds:

```bash
TID=$(curl -s -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  --data '{"name":"aurral","tunnel_type":"caddy"}' \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["id"])')

curl -s -X PUT -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  --data '{"config":{"ingress":[
    {"hostname":"music.bruj0.net","service":"http://127.0.0.1:3001"},
    {"service":"http_status:404"}]}}' \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel/$TID/configurations"

curl -s -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  --data "{\"type\":\"CNAME\",\"name\":\"music.bruj0.net\",\"content\":\"$TID.cfargotunnel.com\",\"proxied\":true,\"ttl\":1}" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records"

curl -s -H "Authorization: Bearer $T" \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/cfd_tunnel/$TID/token"
```

The last call returns the connector token. It is the one the secret holds.

## What the role proves

The postconditions are the application's own answer about itself, not a count
of tasks:

| Assertion | What it catches |
|---|---|
| the container is running | a Compose project that came up with nothing in it |
| `/api/health/live` answers 200 | a process listening that cannot serve |
| `/api/health/bootstrap` reports the version | a container left over from an earlier play |
| the guest's own LAN address answers | a container bound to loopback and published on the wildcard |
| the controller reaches the guest's address | a firewall rule Docker bypassed |
| `aurral.db` exists in the state directory | a container serving with no database behind it |
| the image's `RepoDigests` holds the pinned digest | the registry serving a different build |
