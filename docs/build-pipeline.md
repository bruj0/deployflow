# Build pipeline

How `appctl build` turns a git checkout into the two artifacts a role installs,
and which of them is committed.

## What it produces

```
apps/<app>/files/
  requirements.lock        sha256-pinned dependency tree      COMMITTED
  dist/
    <app>-<version>-py3-none-any.whl                          gitignored
    BUILDINFO                fork, commit, wheel sha256        gitignored
```

The split is deliberate. **The lockfile is committed** because it is a pure
function of the checkout's `pyproject.toml`, so a diff of it means something and a
reviewer can read it. **The wheel is not**, because it is a multi-megabyte
binary that changes on every commit, and `BUILDINFO` records the sha256 that
makes it verifiable. Committing it would buy nothing BUILDINFO does not already
say.

`BUILDINFO` is **YAML**, because the role reads it with `from_yaml`. An earlier
`key=value` format parsed as a bare scalar and failed with
`dictionary update sequence element #0 has length 1; 2 is required`.

Values that could parse as something other than a string are quoted:
`python_target: "3.14"`. Unquoted it reads as a float, and the role asserts it
equals the string `"3.14"`.

## The steps

```
 1  fetch            optional, --fetch
 2  resolve ref      tag, branch or sha → a commit
 3  assert clean     refuse a dirty checkout
 4  export           git archive → a throwaway tree, no .git
 5  seed commit      write _build_commit.txt into the tree
 6  build webui      bun install --frozen-lockfile && bun run build
 7  build wheel      uv build --wheel
 8  verify commit    ask the wheel which commit it is
 9  compile lock     uv pip compile from the checkout's pyproject.toml
10  verify hashes    every entry must carry a sha256
11  publish          copy into files/, write BUILDINFO
```

The build happens in a temp directory from `git archive`, never in your
checkout. Step 6 writes hundreds of packages into `webui/node_modules` and
step 7 writes into `example-app/web/dist`; neither belongs in a tree you also edit.

## The three checks, and why each exists

Each of these exists because a build can quietly produce an artifact that lies.

**A dirty checkout is refused.** The wheel records a commit sha. Built from a
dirty tree, that sha describes code that was never pushed and cannot be
reproduced by anyone else — the provenance would be false. Step 8 then asks the
*wheel* which commit it is, rather than asking the checkout, so the application
states its own provenance rather than having it asserted for it.

**Every lock entry must carry a sha256.** The guest installs with
`--require-hashes`, which fails the whole install if one entry lacks a hash.
Checked here, the error names the package. At install time, pip names nothing.

## Reproducibility

The same commit produces a byte-identical wheel. Measured:

```
$ appctl build example-app && sha256sum apps/example-app/files/dist/*.whl
7cf11926d6329554810538f751e3d6f5dca0f6e265179350710e6f0de2c2a004
```

Rebuilt on a later day from the same commit, the same sha256. That is what makes
`BUILDINFO` a meaningful check rather than a record.

## One change after every build

`BUILDINFO` carries a `built:` timestamp, so rebuilding it changes its content
and the play copies it again — one task reports changed on the first run after a
build. The second run settles to `changed=0`.

That is correct rather than noisy: the record genuinely is new. Use
`appctl build <app> --check` when you want to verify without churning it.

## Commands

```bash
appctl build <app>                # build
appctl build <app> --check        # verify committed artifacts, build nothing
appctl build <app> --dry-run      # resolve the ref and stop
appctl build <app> --ref v1.2     # override source.ref
appctl build <app> --fetch        # git fetch first
```

`--check` is the cheap one. It reports the lockfile's package count, whether
every entry carries a sha256, and the recorded fork commit and wheel sha256,
without building. Run it before deploying to see whether the artifacts on disk
are the ones a build would produce.

## What the role checks

Before installing anything, the role re-reads the sha256 from `BUILDINFO` and
compares it against the wheel that landed on the guest. That check exists
because a truncated copy and a rebuilt wheel both look like a successful copy.

It then confirms the installed package reports the expected commit — read out of
the installed code, because that is the thing which will run.

## When an application needs no build

`source.kind: package` means the wheel already exists on PyPI. `appctl build`
exits non-zero and says so:

```
error: example-app is installed from example-app==1.0.0, so there is nothing to build
```

That is deliberate. Silently succeeding would hide a misconfiguration where
someone expected a build and the version was never pinned.