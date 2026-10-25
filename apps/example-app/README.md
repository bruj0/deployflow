# example-app

The sample application used by the quickstart. It installs in one command and
needs nothing but a Proxmox host:

```bash
appctl install example-app
```

It exists to show what an application role owes the framework, and to be
genuinely runnable — the checks below are the point, and they are the checks
every other role in this repository is modelled on.

An application role is one directory with four obligations:

1. **`defaults/main.yml`** names the application, its port, the package it
   installs and the module it serves. Everything else is derived from
   `app_name`.
2. **`meta/main.yml`** declares the dependency on `common/python`, so adding an
   application never edits a playbook.
3. **`tasks/main.yml`** installs the application and then **proves it works**.
4. **`files/`** holds the pinned lockfile and the module the server imports.

The third is the one that matters. The checks in this role cover the failures a
green play hides: a package tree that did not install, a module that cannot be
imported, a service that is up but bound to the wrong port, and a service that
answers nothing.

## Three things this role gets right that are easy to get wrong

**The lockfile is copied to the guest.** pip runs *on the guest*, and pip on
the guest cannot open a path on the controller. Pointing pip at the
controller's path fails with "Could not open requirements file" — which names
a missing file rather than a file that is present, on a different machine. So
the file travels first, and pip is pointed at where it landed.

**The import check uses a module name, not the application name.**
`example-app` contains a hyphen and is not a valid Python identifier, so
`import example-app` is a *syntax error* rather than a missing module — and
the check would fail for a reason that has nothing to do with the install. The
module is `example_app`, and the unit's `PYTHONPATH` points at the install
root where it lives.

**The role checks the manifest's pin.** The manifest says
`package: "bottle==0.13.3"` and this role installs `bottle==0.13.3`. Those are
two files, and they can drift. The role refuses to run when they disagree,
because a mismatch means the role would install something other than what the
operator reviewed.

## Replace it with your own application, and keep the checks.

See [`.agents/skills/deployflow-add-app/SKILL.md`](../../.agents/skills/deployflow-add-app/SKILL.md)
for the contract a role follows, and [the repository README](../../README.md)
for the workflow as a whole.
