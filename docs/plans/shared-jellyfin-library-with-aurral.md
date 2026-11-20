# Plan: A shared disk for Jellyfin and Aurral, reproducible through deployflow

**Status:** ready for review. The shared-disk feature it depends on is built
and proven; §2 is the measurement record for it.

**What was measured on `kvm.bruj0.net` on 2026-11-20:**

- Two scratch containers (VMID 220, 221) created by `appctl install`, sharing
  one thin volume: the first read-write, the second read-only.
- A file written in the first appeared in the second immediately, owned
  `uid=1000` on both sides.
- The second container could not write, create a directory, or delete the
  first's file: `Read-only file system`, every time.
- A reader running as `uid 107` (the uid Jellyfin's service account uses)
  read what a writer running as `uid 1000` (the uid Aurral's container uses)
  wrote. That is the production shape, not a test shape.
- A second `appctl install` reported `changed=0`.

Everything below that is not marked as a plan is measured. The plan parts are
the parts that have not happened yet.

---

## 1. The goal

Aurral downloads music into a library. Jellyfin serves that music. Today the
music exists once, inside Jellyfin's own root disk, and Aurral's library is
empty:

| | Where it is now | How many files |
|---|---|---|
| Jellyfin's music | `/mnt/media/music` inside CT 100's rootfs | 873 |
| Jellyfin's movies | `/mnt/media/movies` inside CT 100's rootfs | 2 |
| Aurral's library | `/opt/aurral/state/media/downloads/aurral` | 0 |

The target is one copy of every file, on one volume, with Aurral writing and
Jellyfin reading.

```
   data2 (LVM thin pool)
        │
        └── vm-900-disk-0   ← one btrfs volume, 1 TiB
                │
                ├── CT 127  (aurral)   rw    → writes
                └── CT 100  (jellyfin) ro=1  → reads
```

Every part of it is reproducible from the deployflow repository: the volume,
the two attachments, the new Jellyfin container, its tunnel, and
`movies.bruj0.net`.

### What this does not do

- **No copy.** The files move once, from Jellyfin's root disk onto the shared
  volume. There is never a second copy, and there is no sync.
- **No change to Aurral's role.** Aurral keeps its container, its image, its
  database and its tunnel. It gains a bind mount of the shared volume.
- **No change to the Subsonic API, the app contract, or the plugin API.**
- **Jellyfin's database, metadata and thumbnails stay on its own root disk.**
  Only the media moves.

---

## 2. What the proof of concept measured

Everything here was measured on this host. The rules are stated first and the
measurement follows each one, because each rule was a failure before it was a
rule.

### 2.1 The filesystem must be btrfs

ext4 can only be mounted once. The second container's pre-start hook fails:

```
lxc_init: 1037 Failed to run lxc.hook.pre-start for container "211"
__lxc_start: 2208 Failed to initialize container "211"
```

btrfs allows one mount per device, so two containers hold the same volume at
once, one `rw` and one `ro`:

```
CT 220: /dev/mapper/data2-vm--900--disk--0 on /mnt/volume type btrfs (rw,relatime,idmapped,...)
CT 221: /dev/mapper/data2-vm--900--disk--0 on /mnt/volume type btrfs (ro,relatime,idmapped,...)
```

Two other explanations were ruled out first: the failure is not the `idmap`
(with `ro=1` and no idmap it fails identically), and it is not only the
host-side mount (while the volume was also mounted on the host, the container
mount failed too).

### 2.2 The idmap must be identical on every container

btrfs stores **one** idmap per filesystem, in its superblock. The first
container to mount the volume with an idmap decides it, and a second
container asking for a different one is refused:

```
mp0: ...,idmap=u:107:101000:1;g:110:101000:1
→ startup for container '211' failed
```

The same container, alone, with the same different idmap, also fails. It is
not a concurrency problem.

### 2.3 The idmap must be one-to-one, and must not overlap the container's own

Every unprivileged container already has `lxc.idmap = u 0 100000 65536`. A
per-mountpoint idmap is a second mapping on top of that one. A wide
per-mountpoint idmap covering the same range breaks the file ownership:

```
# WRONG
idmap=u:0:101000:65536
→ the shared directory appears inside the container as uid=0 (nobody)
→ the writer cannot create a file: "Permission denied"

# RIGHT
idmap=u:1000:101000:1
→ the shared directory appears as uid=1000
```

So the working form is a **one-to-one** mapping of the single container uid
that owns the files to a single host uid, the same on every container:

```
idmap=u:1000:101000:1;g:1000:101000:1
```

`1000` is Aurral's uid, `101000` is the host uid the files are stored under,
and the range of `1` maps exactly one uid.

**The files on the volume must be owned by the host-side uid.** The idmap
maps ownership; it does not set it. A tree owned by host uid 0 shows up as
`nobody` inside the container, and the writer cannot write to it.

### 2.4 The reader does not need the writer's uid

This is the finding that makes the production shape work. The idmap maps
*host* uid 101000 to *container* uid 1000 on every container that attaches the
volume. The reader does not have to run as uid 1000:

```
$ pct exec 220 -- setpriv --reuid=1000 --regid=1000 --clear-groups \
    sh -c 'echo song > /mnt/volume/music/a.m4a'
WRITE_OK

$ pct exec 221 -- setpriv --reuid=107 --regid=110 --clear-groups \
    cat /mnt/volume/music/a.m4a
song
READ_OK_107

$ pct exec 221 -- setpriv --reuid=107 --regid=110 --clear-groups \
    ls -ln /mnt/volume/music/
-rw-r--r-- 1 1000 1000 5 Nov 20 21:27 a.m4a
```

uid 107 is Jellyfin's service account. uid 1000 is Aurral's container user.
They read and write the same files, through the same idmap, on the same
volume.

The reader's read-only guarantee comes from `ro=1` on the mount, not from the
uid. A reader running as uid 1000 is refused too:

```
$ pct exec 221 -- setpriv --reuid=1000 --regid=1000 --clear-groups \
    touch /mnt/volume/music/y
touch: cannot touch '/mnt/volume/music/y': Read-only file system
```

### 2.5 The sharing semantics, measured

| Action | Writer (rw) | Reader (ro) |
|---|---|---|
| create a file | `WRITE_OK` | — |
| reader sees the new file | — | yes, immediately |
| reader reads the file | — | `READ_OK_107` |
| reader creates a file | — | `Read-only file system` |
| reader creates a directory | — | `Read-only file system` |
| reader deletes the writer's file | — | `Read-only file system` |
| writer deletes its own file | `DELETE_OK` | — |

### 2.6 A shared volume is destroyed with its owner container

A volume is owned by the VMID in its name. `vm-<vmid>-disk-N` belongs to
`<vmid>`, and **destroying that container deletes the volume** — including one
another container is still using:

```
$ pct destroy 212
  Logical volume "vm-212-disk-1" successfully removed.
```

Proxmox states the rule when the *referencing* container is destroyed:

```
$ pct destroy 213
ignore deletion of 'data1:vm-212-disk-1', CT 213 isn't the owner!
```

The fix is a name whose VMID has no container. `vm-900-disk-0` — VMID 900
does not exist and is outside the range the operator allocates from:

```
$ lvrename data1 vm-214-disk-0 vm-900-disk-0
  Renamed "vm-214-disk-0" to "vm-900-disk-0" in volume group "data1"
$ pct destroy 214
$ lvs data1 | grep vm-900-disk-0
  vm-900-disk-0    data1        <- SURVIVED
```

**Volume names must also be in Proxmox's convention.** A name Proxmox cannot
parse is refused when it is attached:

```
$ pct set 212 -mp0 data1:poc-orphan-disk,mp=/mnt/shared
Could not rescan volume size - unable to parse lvm volume name 'poc-orphan-disk'
```

### 2.7 ext4 converts to btrfs in place, with the files preserved

```bash
pct stop 100
e2fsck -f -y /dev/data2/vm-100-disk-0
btrfs-convert /dev/data2/vm-100-disk-0
```

Measured on a test volume:

```
$ btrfs-convert /dev/data1/poc-shared
Create initial btrfs filesystem
Create ext2 image file
Create btrfs metadata
Copy inodes [o] [ 14/14]
Conversion complete

$ blkid /dev/data1/poc-shared
/dev/data1/poc-shared: LABEL="pocshared" UUID="c249a9f4-..." TYPE="btrfs"
```

The payload survived byte for byte, and the converted volume then mounted in
two containers at once.

Two details about the tool: it is the **standalone binary**
`/usr/bin/btrfs-convert` in btrfs-progs 6.14, not a `btrfs convert`
subcommand; and it has **no `-f` flag** — `-f` is parsed as `--features` and
the command exits with a usage message. The conversion is reversible with
`btrfs-convert -r`.

### 2.8 The host can mount the volume while the containers hold it

This was the one rule the earlier proof of concept got wrong, and it is
measured now because the role needs it.

```
$ mount /dev/data2/vm-900-disk-0 /srv/shared-disk/vm-900-disk-0
$ ls -ln /srv/shared-disk/vm-900-disk-0/music/
-rw-r--r-- 1 101000 101000 0 Nov 20 21:19 final.mp3
```

The host sees the **host-side** ownership, and the guests see theirs. The
idmap is applied on the container's mount, not stored in the filesystem's
superblock:

```
$ btrfs inspect-internal dump-super /dev/data2/vm-900-disk-0 | grep -i idmap
(no output)
```

This is what lets the role prepare the tree's ownership without stopping the
guests.

### 2.9 The mount path does not select a subtree

```
$ pct set 216 -mp0 ...,mp=/mnt/music
→ the container sees the volume ROOT (both `movies` and `music`)
```

Proxmox's allowed mountoptions are a fixed list, and `subvol=` is refused. A
subtree is selected by the application, not by the mount. Docker can bind a
subtree of an idmapped mount:

```
$ docker run --rm -v /mnt/volume/music:/data alpine stat -c '%n uid=%u' /data/a.mp3
/data/a.mp3 uid=1000
```

### 2.10 `pct create` fails when the caller has a umask of 077

```
tar: /var/lib/lxc/210/rootfs: Cannot open: Permission denied
unable to create CT 210 - command 'lxc-usernsexec -m u:0:100000:65536 ...'
  failed: exit code 2
```

`PVE::LXC::mount_all` calls `File::Path::make_path`, which honors the
process umask. Under umask 077 the directory comes out `drwx------` and
`tar` cannot `chdir` into it.

`run-remote` sets umask 077; a plain SSH session gives 0022. **Deployflow is
unaffected** — Ansible's `become` path gives 0022, which is why the role has
never hit this. It matters when driving `pct` by hand.

---

## 3. What the deployflow feature is

The feature is built and proven. This section is its shape, so the plan below
reads against it.

### 3.1 The manifest declares the disk

`app.yaml`, a new top-level block:

```yaml
shared_disks:
  media:
    storage: data2
    size_gb: 1000
    volume: vm-900-disk-0
    filesystem: btrfs
    idmap: "u:1000:101000:1;g:1000:101000:1"
    host_uid: 101000
    trees:
      - /music
      - /movies
```

The schema rejects, before anything is created:

| Rejected | Why |
|---|---|
| `filesystem: ext4` | §2.1 |
| a volume name that is not `vm-<number>-disk-<n>` | §2.6 |
| a missing `idmap` | §2.3 |
| an idmap overlapping `0..65536` or `100000..165535` | §2.3 |
| a relative tree path | the tree is created on the volume |
| two disks claiming one volume | they would fight over it |

### 3.2 The guest declares the attachment

The attachment lives in the guest's `group_vars`, next to the guest facts it
belongs with:

```yaml
# inventory/group_vars/jf/main.yml
shared_disk_mounts:
  - disk: media
    mountpoint: /mnt/volume
    read_only: true
```

**The attachments do not travel as play variables.** Extra vars are global,
so passing them would attach every guest's mounts to every guest. Only the
disks travel as a variable; the role joins the two per guest.

### 3.3 The role prepares the volume

`common/shared_disk` runs on the Proxmox node, once, before the guests. It:

1. reads the storage's thin pool (`pvesh get /storage/data2` → `thinpool`);
2. creates the thin volume if it does not exist;
3. refuses a volume that is not btrfs;
4. formats a volume it just created;
5. mounts it on the node, creates the trees, and gives them the host-side
   ownership;
6. asserts the ownership;
7. unmounts it.

It is idempotent: a second run finds everything and changes nothing.

### 3.4 The guest role attaches the volume

`common/lxc`, between "Create the container" and "Start the container":

```
pct set <vmid> -mp0 data2:vm-900-disk-0,mp=/mnt/volume,idmap=u:1000:101000:1;g:1000:101000:1[,ro=1]
```

The task reads `pct config` first and skips the attach when the line is
already there. The comparison is a prefix match, because Proxmox appends
`size=` to the line it stores:

```
written: mp0: data2:vm-900-disk-0,mp=/mnt/volume,idmap=u:1000:101000:1;g:1000:101000:1
stored:  mp0: data2:vm-900-disk-0,mp=/mnt/volume,idmap=u:1000:101000:1;g:1000:101000:1,size=1000G
```

The attach runs before the container starts, because Proxmox attaches the
volume when it starts the guest, and a guest that starts without its shared
disk is a guest serving an empty library.

### 3.5 The proof

| Check | Result |
|---|---|
| `appctl install` from a clean host | 13 changed, 0 failed |
| `appctl install` again | **0 changed, 0 failed** |
| writer creates a file | `WRITE_OK` |
| reader sees it, as uid 107 | `READ_OK_107` |
| reader writes | `Read-only file system` |
| reader deletes the writer's file | `Read-only file system` |
| writer deletes its own file | `DELETE_OK` |
| host sees host-side uid 101000 | yes (§2.8) |

84 unit tests pass, including 11 new ones for this feature.

---

## 4. The design

### 4.1 The volume

`data2:vm-900-disk-0`, 1 TiB, btrfs, ownerless name (§2.6).

It is the **current** `data2:vm-100-disk-0`, converted and renamed in place.
That is what makes "one copy, no copy at all" true: the 873 files and the
4.9 GB they occupy move by a filesystem conversion, not by a copy.

The size stays 1 TiB. The current volume is 1007 G with 19 G used, so the
conversion needs no growth.

### 4.2 The idmap

`u:1000:101000:1;g:1000:101000:1` on **both** guests (§2.2, §2.3).

`host_uid: 101000` is the uid the files are stored under on the host. It
sits above the container's own range (65536) and below Proxmox's reserved
subuid range (100000–165535), so it is outside both.

### 4.3 The ownership move

The media currently belongs to uid 107:110 — Jellyfin's service account,
inside the container. On the host that is uid 100107:100110. The idmap maps
host uid 101000 to container uid 1000, so the files have to be re-owned on the
host:

```
$ chown -R 101000:101000 /srv/shared-disk/vm-900-disk-0/music
```

This is the one destructive step. It rewrites the ownership of 873 files and
2 directories, and nothing else. The files themselves do not change.

### 4.4 The new Jellyfin container

Jellyfin moves from CT 100 to a new guest that deployflow owns.

| | Today | After |
|---|---|---|
| VMID | 100 | 100 (reused) |
| Root disk | `data2:vm-100-disk-0` (1 TiB) | `data1:vm-100-disk-0` (16 G) |
| Shared disk | — | `data2:vm-900-disk-0`, `ro=1` |
| Media path | `/mnt/media/music` | `/mnt/volume/music` |
| Version | 10.11.11 | 12.4 |
| Service account | jellyfin (uid 107) | jellyfin (uid 107) |

**The new root disk goes on `data1`.** `data2` holds the shared volume; the
root disk is a different thing and `data1` is where the other guests' root
disks live.

**The version is 12.4.** Measured on the current container after an
`apt-get update`:

```
$ apt-cache policy jellyfin
jellyfin:
  Installed: 10.11.11+ubu2404
  Candidate: 12.4+ubu2404
```

12.4 is a major version jump from 10.11. It changes the database schema.
Jellyfin migrates it on first start, and the migration is one-way: a 10.11
server cannot read a 12.4 database. §6 handles that by taking a copy of the
old database before the cutover.

### 4.5 What Jellyfin's role does

A new `apps/jellyfin/` role, modeled on `apps/aurral/`. It is a **systemd
package** application, not a container, so it is its own shape:

| | |
|---|---|
| Install | the `jellyfin` apt package from `repo.jellyfin.org` |
| Service | `jellyfin.service`, already shipped by the package |
| Config | `/etc/jellyfin`, owned by the service account |
| State | `/var/lib/jellyfin` — the database, metadata and thumbnails |
| Media | the shared volume, read-only, mounted at `/mnt/volume` |

Three things the role must do that are not obvious:

1. **Disable the metadata savers.** Jellyfin writes thumbnails and metadata
   into the media folder by default. On a read-only mount that is an
   `IOException: Read-only file system` on every library scan
   (jellyfin/jellyfin#6605). The role sets:

   ```xml
   <SaveImageTagsInPortableMode>false</SaveImageTagsInPortableMode>
   <SaveAlbumArtInPortableMode>false</SaveAlbumArtInPortableMode>
   <SaveThumbsInPortableMode>false</SaveThumbsInPortableMode>
   ```

   and points `MetadataPath` at the state directory, not the media folder.

2. **Own the service account.** The package creates `jellyfin` (uid 107) on
   install. The role asserts that uid, because the media's ownership inside
   the container depends on it (§2.4).

3. **Assert the library is readable.** The role's own postcondition: Jellyfin
   answers on its port, the shared mount is present and `ro`, and the media
   tree is visible to the service account. A green play means the server can
   actually serve the library.

### 4.6 Jellyfin's tunnel

Jellyfin gets **its own** Cloudflare tunnel, on `movies.bruj0.net`, pointed at
its own port. Aurral's tunnel stays as it is, on `music.bruj0.net`.

The Aurral role already implements a locally managed tunnel. The Jellyfin
role reuses the pattern:

- `jellyfin_tunnel_id` — the tunnel's identifier, in the manifest;
- `jellyfin_tunnel_token` — the connector token, in `.env` via
  `appctl secrets set`;
- `jellyfin_tunnel_config_file` — the ingress file, mode 0644, no credential
  in it;
- `jellyfin_tunnel_hostname` — `movies.bruj0.net`.

The origin is the guest's own loopback:

```yaml
ingress:
  - hostname: movies.bruj0.net
    service: http://127.0.0.1:8096
```

A DHCP lease can move and an origin naming the guest's address breaks when it
does. Loopback cannot.

The role waits for `Registered tunnel connection` in the connector's log, and
fails if it does not appear. A connector that started but cannot authenticate
retries forever while the hostname stays dead — a green play over a broken
service is worse than a red one.

### 4.7 Aurral's side

Aurral's role gains one bind mount and one change to its Compose file.

The shared volume is attached to CT 127 read-write at `/mnt/volume`. The
Compose project binds the **music subtree** of it, so Aurral's container
never sees the movies:

```yaml
    volumes:
      - /opt/aurral/state/config:/config
      - /mnt/volume/music:/data/downloads/aurral
```

The current line is `/opt/aurral/state/media:/data`, and Aurral's Library
folder is `/data/downloads/aurral`. The new line mounts the shared tree
directly at that path, so Aurral's own Library folder setting does not change
and the container sees exactly the same path it sees today.

`/mnt/volume/music` is a subtree of an idmapped mount. Docker binds it
correctly (§2.9), measured:

```
$ docker run --rm -v /mnt/volume/music:/data alpine stat -c '%n uid=%u' /data/a.mp3
/data/a.mp3 uid=1000
```

The `aurral` container runs as uid 1000, which is the uid the idmap maps to,
so Aurral writes files the reader can see.

### 4.8 What does not change

- `common/lxc`, `common/python`, the targets, and the playbooks. The new
  Jellyfin guest is a block in `inventory/hosts.yml` and a role under
  `apps/`, like every other application.
- The `proxmox_lxc` target's mount-point handling. The attachment is in the
  guest's `group_vars`, not in the target's options, because a target's
  options are global and an attachment is per-guest.
- Aurral's container, image, database, API contract and plugin API.

---

## 5. The steps

### Step 1 — take a copy of Jellyfin's state

Not the media — the conversion moves that in place. The state is the
database, the configuration and the metadata:

```bash
run-remote kvm.bruj0.net -- pct exec 100 -- tar czf - -C /var/lib jellyfin
```

176 MB. It goes into the private `deployflow-state` repository, which is
where everything else that is not in git already lives. This is the only
rollback path for a 12.4 schema migration.

### Step 2 — stop CT 100 and confirm it is quiescent

```bash
run-remote kvm.bruj0.net -- pct stop 100
```

`systemctl is-active jellyfin` must report `inactive` before the next step.
The container must be stopped, not just the service: the media is inside its
root disk.

### Step 3 — convert the rootfs from ext4 to btrfs, in place

```bash
run-remote kvm.bruj0.net -- e2fsck -f -y /dev/data2/vm-100-disk-0
run-remote kvm.bruj0.net -- btrfs-convert /dev/data2/vm-100-disk-0
```

`btrfs-convert` preserves the files (§2.7). There is no `-f` flag.

**Check before continuing:**

```bash
run-remote kvm.bruj0.net -- blkid -o value -s TYPE /dev/data2/vm-100-disk-0
# btrfs
```

and

```bash
run-remote kvm.bruj0.net -- mkdir -p /srv/shared-disk/vm-900-disk-0
run-remote kvm.bruj0.net -- mount /dev/data2/vm-100-disk-0 /srv/shared-disk/vm-900-disk-0
run-remote kvm.bruj0.net -- ls /srv/shared-disk/vm-900-disk-0/mnt/media/music | wc -l
# 873
run-remote kvm.bruj0.net -- umount /srv/shared-disk/vm-900-disk-0
```

If the count is not 873, stop here and roll back with `btrfs-convert -r`.

### Step 4 — give the volume an ownerless name

```bash
run-remote kvm.bruj0.net -- lvrename data2 vm-100-disk-0 vm-900-disk-0
```

Proxmox refuses to rename a volume a container still holds, so CT 100 must
be stopped first (Step 2 did that).

### Step 5 — destroy CT 100, keeping the volume

```bash
run-remote kvm.bruj0.net -- pct destroy 100
```

The volume survives because its name no longer carries CT 100's VMID
(§2.6). Check:

```bash
run-remote kvm.bruj0.net -- lvs data2 | grep vm-900-disk-0
# vm-900-disk-0   data2   1.00t
```

### Step 6 — move the media to the shared tree's ownership

```bash
run-remote kvm.bruj0.net -- mount /dev/data2/vm-900-disk-0 /srv/shared-disk/vm-900-disk-0
run-remote kvm.bruj0.net -- mkdir -p /srv/shared-disk/vm-900-disk-0/music /srv/shared-disk/vm-900-disk-0/movies
run-remote kvm.bruj0.net -- mv /srv/shared-disk/vm-900-disk-0/mnt/media/music/. \
    /srv/shared-disk/vm-900-disk-0/music/
run-remote kvm.bruj0.net -- mv /srv/shared-disk/vm-900-disk-0/mnt/media/movies/. \
    /srv/shared-disk/vm-900-disk-0/movies/
run-remote kvm.bruj0.net -- chown -R 101000:101000 /srv/shared-disk/vm-900-disk-0/music \
    /srv/shared-disk/vm-900-disk-0/movies
run-remote kvm.bruj0.net -- chmod 0755 /srv/shared-disk/vm-900-disk-0/music \
    /srv/shared-disk/vm-900-disk-0/movies
run-remote kvm.bruj0.net -- umount /srv/shared-disk/vm-900-disk-0
```

The `mv` is a rename inside one filesystem, so the 4.9 GB does not move.

The `chown` is the one destructive step (§4.3).

**Check:**

```bash
run-remote kvm.bruj0.net -- mount /dev/data2/vm-900-disk-0 /srv/shared-disk/vm-900-disk-0
run-remote kvm.bruj0.net -- ls -ln /srv/shared-disk/vm-900-disk-0/music | head -3
# -rw-r--r-- 1 101000 101000 ... 14 Years - Guns N' Roses.m4a
run-remote kvm.bruj0.net -- ls /srv/shared-disk/vm-900-disk-0/music | wc -l
# 873
run-remote kvm.bruj0.net -- umount /srv/shared-disk/vm-900-disk-0
```

### Step 7 — declare the disk and the guests

`app.yaml`:

```yaml
shared_disks:
  media:
    storage: data2
    size_gb: 1000
    volume: vm-900-disk-0
    filesystem: btrfs
    idmap: "u:1000:101000:1;g:1000:101000:1"
    host_uid: 101000
    trees:
      - /music
      - /movies
```

`inventory/hosts.yml` — a new guest and a new group:

```yaml
        jellyfin:
          ansible_user: root
          lxc_vmid: 100
          lxc_hostname: jellyfin
```

```yaml
    jf:
      hosts:
        jellyfin:
```

`inventory/group_vars/jf/main.yml` — the guest's facts and its attachment:

```yaml
---
app_roles:
  - jellyfin

lxc_vmid: 100
lxc_hostname: jellyfin
lxc_cores: 2
lxc_memory: 2048
lxc_swap: 512
lxc_rootfs_gb: 16
lxc_storage: data1
lxc_onboot: 1

# The shared media volume, attached read-only.
shared_disk_mounts:
  - disk: media
    mountpoint: /mnt/volume
    read_only: true
```

`inventory/group_vars/au/main.yml` — Aurral's attachment:

```yaml
# The same volume, attached read-write.
shared_disk_mounts:
  - disk: media
    mountpoint: /mnt/volume
    read_only: false
```

### Step 8 — write the Jellyfin role

`apps/jellyfin/` with `defaults/main.yml`, `meta/main.yml` and
`tasks/main.yml`, modeled on `apps/aurral/`. The role:

1. installs the `jellyfin` package from `repo.jellyfin.org`;
2. asserts the service account is uid 107;
3. creates `/var/lib/jellyfin` and `/etc/jellyfin` with the right ownership;
4. writes `system.xml` with the metadata savers disabled and
   `MetadataPath` pointing at the state directory;
5. restores the database from the copy in Step 1;
6. points the libraries at `/mnt/volume/music` and `/mnt/volume/movies`;
7. installs the tunnel connector and its ingress file;
8. starts Jellyfin and the tunnel;
9. asserts the library is readable and the tunnel registered.

### Step 9 — install

```bash
cd deployflow
PYTHONPATH=src python3 -m appctl install jellyfin
```

The play order is: the shared-disk role creates the volume (it already
exists, so it changes nothing), the `lxc` role creates the guest and attaches
the volume read-only, and the Jellyfin role installs the server.

**Check that the volume was attached read-only:**

```bash
run-remote kvm.bruj0.net -- pct config 100 | grep mp0
# mp0: data2:vm-900-disk-0,mp=/mnt/volume,idmap=u:1000:101000:1;g:1000:101000:1,ro=1,size=1000G
```

### Step 10 — attach the volume to Aurral

```bash
cd deployflow
PYTHONPATH=src python3 -m appctl install aurral
```

### Step 11 — the tunnel and the hostname

Create the named tunnel in the Cloudflare dashboard, put its id in the
manifest and its token in `.env`:

```bash
appctl secrets set jellyfin_tunnel_token '<token>'
```

`jellyfin_tunnel_id` goes in `app.yaml`'s Jellyfin block. Then re-run the
install so the role writes the ingress file and starts the connector.

Point `movies.bruj0.net` at the tunnel's hostname in the Cloudflare DNS.

**Check:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://movies.bruj0.net/
```

### Step 12 — verify

```bash
# the reader sees the files
run-remote kvm.bruj0.net -- pct exec 100 -- su -s /bin/sh jellyfin -c \
  'ls /mnt/volume/music | wc -l'
# 873

# the reader cannot write
run-remote kvm.bruj0.net -- pct exec 100 -- su -s /bin/sh jellyfin -c \
  'touch /mnt/volume/music/x'
# touch: cannot touch '/mnt/volume/music/x': Read-only file system

# the writer writes, and the reader sees it
run-remote kvm.bruj0.net -- pct exec 127 -- docker exec aurral sh -c \
  'touch /data/downloads/aurral/probe.m4a'
run-remote kvm.bruj0.net -- pct exec 100 -- ls /mnt/volume/music/probe.m4a
# /mnt/volume/music/probe.m4a

# clean up the probe
run-remote kvm.bruj0.net -- pct exec 127 -- docker exec aurral rm \
  /data/downloads/aurral/probe.m4a

# the endpoints
curl -s -o /dev/null -w "%{http_code}\n" https://movies.bruj0.net/
curl -s -o /dev/null -w "%{http_code}\n" https://music.bruj0.net/
```

---

## 6. The cutover, in order

The order matters, and each step is reversible only until the next one.

| Step | What | Reversible? |
|---|---|---|
| 1 | Copy Jellyfin's state | yes, it is a copy |
| 2 | Stop CT 100 | yes, `pct start 100` |
| 3 | Convert ext4 → btrfs | yes, `btrfs-convert -r` |
| 4 | Rename the volume | yes, `lvrename` back |
| 5 | Destroy CT 100 | **no** — the state copy is the only way back |
| 6 | Move and re-own the media | yes, the files are on the volume either way |
| 7 | Declare the disk and the guests | yes, a file edit |
| 8 | Write the Jellyfin role | yes, a file edit |
| 9 | Install Jellyfin | yes, `pct destroy 100` |
| 10 | Attach the volume to Aurral | yes, `pct set --delete mp0` |
| 11 | The tunnel and the hostname | yes |
| 12 | Verify | — |

**The point of no return is Step 5.** Before it, `pct start 100` brings
everything back. After it, the only way back is the Step 1 copy plus
`btrfs-convert -r` plus the original root disk — which is why Step 1 exists.

### Rollback

Before Step 5:

```bash
run-remote kvm.bruj0.net -- pct stop 100
run-remote kvm.bruj0.net -- umount /srv/shared-disk/vm-900-disk-0 2>/dev/null
run-remote kvm.bruj0.net -- lvrename data2 vm-900-disk-0 vm-100-disk-0
run-remote kvm.bruj0.net -- btrfs-convert -r /dev/data2/vm-100-disk-0
run-remote kvm.bruj0.net -- pct start 100
```

After Step 5: the media is still on the volume and the state is in the copy.
Recreate CT 100 from a stock template, restore the state copy, and either
keep the volume as the media source or convert it back.

---

## 7. The risks

| Risk | How bad | What is done about it |
|---|---|---|
| The 12.4 schema migration fails or loses data | The library's metadata and user accounts | The database is copied first (§5 Step 1). Jellyfin's migration is one-way, so the old container cannot read the new database — the copy is the only way back. |
| The conversion loses a file | The media itself | `btrfs-convert` preserves files byte for byte (§2.7). The file count is checked before and after (§5 Steps 3 and 6). A test volume was converted and compared. |
| The `chown` rewrites the wrong ownership | Aurral cannot write | The count and the owner are checked after (§5 Step 6). The role asserts the ownership on every later run. |
| The idmap overlaps the container's own range | The tree appears as `nobody`, nothing works | The schema rejects it (§3.1). |
| The second guest mounts with a different idmap | The second container refuses to start | Both guests use the same idmap from one manifest entry (§4.2). |
| The volume is deleted with a container | The media | The name carries an unused VMID (§2.6). The role refuses a name Proxmox cannot parse. |
| Jellyfin's metadata savers write to a read-only mount | Every library scan throws | The role disables them (§4.5). |
| The tunnel is remotely managed and overwrites the ingress file | `movies.bruj0.net` points somewhere else | The role writes a **locally** managed tunnel, and says so in the file (§4.6). |
| The guest's DHCP lease moves | The tunnel's origin breaks | The origin is the guest's loopback (§4.6). |

---

## 8. The questions

1. **Should the movies move too, or only the music?** Both are on the same
   volume and both are small (2 files, 4.9 GB for the music). Moving both
   keeps one volume and one attachment. Moving only the music would need a
   second volume and a second attachment for Jellyfin.

2. **Should Aurral's library path change?** The plan binds the shared tree
   at `/data/downloads/aurral`, which is Aurral's Library folder today, so
   Aurral's own setting does not change. The alternative is to bind at
   `/data` and move the tree under `downloads/aurral` on the volume, which
   adds a directory level the shared volume does not otherwise need.

3. **Should the 12.4 jump happen now, or separately?** The plan does both at
   once, because the container is being recreated anyway and a second
   cutover would mean two stops and two restarts of the media. Splitting it
   means creating the new container on 10.11 first, then upgrading.

4. **Should the shared disk be a separate volume from the root disk?** It is
   one volume, and the root disk is separate (`data1`, 16 G). A single
   volume that carries both the media and Jellyfin's state would make the
   state move with the media, which is what this plan avoids.

5. **Should the tunnel be created by the role, or by hand in the Cloudflare
   dashboard?** The role can only be handed a token. Creating the named
   tunnel needs the API, and that is a separate credential from the
   connector's. Aurral's tunnel was made by hand; this plan does the same.
