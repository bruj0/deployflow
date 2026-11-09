# homarr

Homarr, **the dashboard**, as one container on one guest.

Homarr is a single image: a Next.js server, an internal Redis and an nginx
front end, all started by the image's own entrypoint. SQLite is the default and
lives inside `/appdata`, so there is no database server to run beside it. That
makes this the simplest container application in this repository — `proxcenter`
runs two images and a Compose file it fetches from upstream, and this one runs
one image and a Compose file the role writes itself.

## What is deployed

| | |
|---|---|
| Guest | `homarr`, CT 126 |
| Image | `ghcr.io/homarr-labs/homarr@sha256:44c480af…`, pinned by digest |
| Version | `2.12.0` |
| Install root | `/opt/homarr/app`, beside the inert Python venv |
| Compose project | `/opt/homarr/app/project/docker-compose.yml`, written by this role |
| Environment | `/opt/homarr/app/project/.env`, mode `0600`, root-owned |
| Unit | `homarr.service` |
| Port | 7575 |
| State | `/opt/homarr/state/appdata` — the SQLite database, the Redis dump and the trusted certificates |
| Secrets | none in `.env`. The encryption key is generated on the guest, once, and kept |

## Install it

```bash
appctl install homarr
appctl install homarr        # must report changed=0
```

The manifest key is `homarr` and the role directory is `apps/homarr/`. They
match: `appctl install` selects the role with `--tags lxc,<key>`, and a key that
is not a role name selects nothing — the play runs, changes nothing and reports
success.

## Why `kind: package` for a container image

The manifest schema has three source kinds and requires a `build:` block for
`repo`. `package` is its word for "a released artifact pinned by version",
which is what `proxcenter` already uses for its container images.

The benefit is concrete rather than convenient. `appctl` splits
`homarr==2.12.0` and passes both halves into the play as
`appctl_source_package` and `appctl_source_version`, so the role can assert that
the manifest and its own pin agree. A `name@version` form carries no comparison,
and the assertion silently checks nothing.

## The image is pinned by digest, and the pin is checked

`homarr_image` in `apps/homarr/defaults/main.yml` names a digest, not a tag.
`latest` and `v2.12.0` resolved to the same digest on 2026-11-09, which is
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

## The encryption key is generated once and never regenerated

`SECRET_ENCRYPTION_KEY` encrypts every stored integration credential. A
regenerated key does not fail the install — it makes every saved secret
undecryptable, silently, with the symptom "the integration I saved has stopped
working" rather than "the install failed".

So the role reads the existing `.env` before writing it and keeps the value it
finds. The key is generated on the guest rather than kept in `.env` on the
controller, because it has no external issuer and because a key in `.env` is a
key that can be rotated by accident.

It must be **64 hex characters**. Homarr validates it at startup and exits with
`SECRET_ENCRYPTION_KEY is required, please generate a 64 character secret in
hex format` otherwise. The role generates lowercase hex, which is what
`openssl rand -hex 32` — the command Homarr's own documentation gives —
produces.

## The Docker socket is off by default

Mounting `/var/run/docker.sock` into a container gives that container root on
the guest: it can start a privileged container, mount the host filesystem and
read every other container's environment. Homarr uses it only for its Docker
integration — discovering containers to show as board tiles.

`homarr_mount_docker_socket` is `false`, and turning it on is a deliberate act
in the guest's `group_vars`:

```yaml
# inventory/group_vars/hm/main.yml
homarr_mount_docker_socket: true
```

## The firewall is a DOCKER-USER rule, not a ufw rule

Docker installs its own chains in nat/filter and evaluates them before ufw's
INPUT chain, so a ufw rule that denies a published port is reported as applied
by `ufw status` and enforced by nothing. `DOCKER-USER` is the chain Docker
documents for exactly this, and it is evaluated before the DNAT that publishes
the port.

`homarr_lan_cidr` defaults to `10.0.0.0/8`, which is the range `common/lxc` puts
guest `vnet0` on. On its own that documents intent rather than restricting
anything — narrow it to the operator's own subnet to make it a real limit.

## What the role proves

The postconditions are the application's own answer about itself, not a count of
tasks:

| Assertion | What it catches |
|---|---|
| `docker compose ps` reports `homarr:running` | a container that exited after `up` returned |
| `/api/health/live` answers 200 | a server that is listening but cannot reach its database or its Redis |
| the board answers on the guest's own address | a container published on `127.0.0.1` while the role believes it published on `0.0.0.0` |
| the board answers from the controller | a firewall rule Docker bypassed |

`/api/health/live` is the image's own `HEALTHCHECK` path. It answers 200 only
when the database and the internal Redis both respond, so a 200 is the
application's statement that it can serve a board — not merely that a process is
listening.

## First visit

`/` answers `307` to `/init` until the first account exists. The onboarding flow
creates it, and the default board (`dashboard`, seven widgets) is seeded by the
container's own entrypoint on first boot.
