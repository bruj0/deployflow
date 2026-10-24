# Delivery targets

A target says **where and how an application runs**. It is not the agent that
wrote the manifest, and it is not the application.

The manifest names one:

```yaml
applications:
  example-app:
    target:
      kind: proxmox_lxc
      options:
        hostname: example-app
        vmid: 121
        bind_host: 127.0.0.1
```

## What a target is for

Without the idea of a target, everything about a deployment is one thing. The
tool knows about Proxmox, the roles know about Proxmox, the config knows about
containers. Adding a virtual machine target then means touching all of it.

The interface separates the parts that are the same everywhere from the parts
that differ:

| Shared | Target-specific |
|---|---|
| Source and artifact | Where the destination lives |
| Configuration rendering | How the destination is created or found |
| Secret handling | How the artifact is installed |
| State recording | How the application is checked |
| The manifest schema | Which checks make sense here |

An application declares what it is. A target decides where it runs.

## The interface

Five steps, in `src/appctl/targets/base.py`:

```python
class Target:
    kind: str          # what the manifest names
    summary: str       # one line, shown by `appctl targets`
    capabilities: tuple[str, ...]

    def validate(self, context): ...      # check settings and prerequisites
    def provision(self, context): ...     # create or identify the destination
    def deploy(self, context, artifacts, rendered): ...   # install and check
```

Each method is a step the first target actually performs. There is no method
that returns "not supported", because a promise nobody keeps is worse than an
absent method.

### `validate`

Called before any build, download or execution. Everything it can reject is
rejected here, where nothing has happened yet:

- a missing setting, named with the key that is missing;
- a missing prerequisite, named with the command that would fix it;
- an application that is not wired to a destination.

### `provision`

Creates the destination or reports where it is. For `proxmox_lxc` the guest is
created by the first play rather than here: creating it needs `pct` on the
Proxmox host, and the play already does that, guarded by a stat so a second run
is a no-op.

### `deploy`

Installs the artifact, runs the application's own checks, and returns what it
observed. The result carries the counts, the checks and the log directory, so
the caller does not need to know how the target worked.

## The registry

`src/appctl/targets/registry.py` holds two lists:

- **implemented** targets, which can be used;
- **planned** targets, which cannot.

```python
registry.resolve("proxmox_lxc")   # returns the target
registry.resolve("vm")            # TargetError: planned but not implemented
registry.resolve("mainframe")     # TargetError: unknown target kind
```

The resolution never falls back. An application that asks for a virtual
machine must not be silently installed into a container, and the only way to
guarantee that is to fail.

## The implemented target: `proxmox_lxc`

Everything Proxmox-specific lives in `src/appctl/targets/proxmox_lxc.py`:

- the container ID and the guest hostname;
- the template, the storage, the bridge and the guest network;
- the `pct` operations that create the guest and discover its DHCP lease;
- the SSH connection to the guest.

The shared code above it knows none of them.

## Writing a target

The interface is small enough that a new target is a single file. It has to
answer four questions honestly:

1. **What are its settings?** They go in `target.options`. The shared schema
   does not read inside them, so a new target never changes `config.py`.
2. **What must be true before it runs?** `validate` checks it and says what is
   wrong.
3. **How does it install the artifact?** `deploy` does it, using the same
   rendered configuration and the same secret values as every other target.
4. **What proves the application works?** The application's own checks, plus
   whatever the target adds.

### What a target must not do

- **It must not decide what to install.** It receives a built artifact and
  hands it over.
- **It must not weaken a check.** A target that cannot run an application's
  checks has to say so, not skip them.
- **It must not pretend.** A target that implements part of an operation
  should implement the whole of it or not claim it.

## The planned targets

Neither is implemented. Both are named in the registry so that a manifest can
name them and get a clear answer.

### `vm`

Would provision a virtual machine and install the application into it.

It would need: machine creation or selection, boot and address discovery, guest
access, and a way to install an artifact that is not a wheel. It would also need
its own checks, because "the VM is running" is not "the application works".

### `docker_vm`

Would provision a virtual machine and run the application in Docker there.

It would need everything `vm` needs, plus: Docker availability on the guest,
image provenance, runtime configuration, ports and volumes, and container
health. A container that starts is not an application that answers.

### What each would have to prove

A planned target becomes supported when:

- the same application intent installs on it and on `proxmox_lxc`;
- the application's own checks pass there;
- a second install reports no change;
- one deliberate failure is caught by a check, not by a red play.

Until then it stays in the planned list. The README says the same thing, so a
reader is not misled.
