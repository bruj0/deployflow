# Secrets

Where a secret lives, how it travels, and the three places it can leak.

## The model

The full path of a value is drawn as a sequence diagram in
[architecture.md](architecture.md#where-a-secret-goes). In short:

| Where | State |
|---|---|
| `.env` on the laptop | the only plain-text copy, mode 0600, gitignored |
| the temp extra-vars file | mode 0600, passed as `--extra-vars @file`, deleted at exit |
| the guest's EnvironmentFile | mode 0600, owned by root |
| the application's memory | resolved from that file at startup |
| **any log** | **never** — see [the three leaks](#the-three-leaks-and-the-guards) |

A secret is **never** written to `app.yaml`, to a rendered config file, or to a
log. In a config file it appears only as a `${NAME}` reference, which the guest
resolves at startup. That is what makes a config file safe to commit.

## Commands

```bash
appctl secrets init              # writes .env (0600) and .env.example
appctl secrets check             # what is set; exit 1 while anything required is missing
appctl secrets list              # names and state, never values
appctl secrets set NAME 'value'  # positional
appctl secrets set NAME          # prompts, value never in argv or shell history
appctl secrets set NAME --generate
```

`set` rewrites the file in place, preserving existing keys and their order, so
running it twice for one key leaves one entry. A duplicate `KEY` in a `.env`
file is a well-known source of "I changed it and nothing happened".

Values needing spaces or `#` are quoted automatically. Read them back with
`dotenv` if you need to.

## Why not ansible-vault

The vault file this repository used was encrypted with a throwaway password that
was then deleted. It became undecryptable and unrecoverable from the
repository, and every run failed at:

```
[ERROR]: Attempting to decrypt but no vault secrets found
```

A secret store you can permanently lock yourself out of is a worse default than
a plain file that is simply not committed. The trade is real: `.env` has no
second factor. That is why it is mode 0600, gitignored, and why
`.env.example` — the list of required keys, with no values — *is* committed, so
a fresh clone knows what to fill in.

Do not reintroduce a vault without a password the operator actually holds.

## The three leaks, and the guards

### 1. In `argv`

An extra variable passed as `-e key=value` is visible in `ps` for the whole run,
to every process on the machine.

**Guard.** Secrets go in a mode-0600 temporary file passed as `--extra-vars
@file`. The file is deleted when the run ends, including on failure.

### 2. In Ansible's own echo

**Measured.** At `-vvv`, a secret passed as an extra variable appears twice:

```yaml
extra_vars: ('myapp_api_key=PLAINTEXT',)      # Ansible echoing our own argv
...
"myapp_api_key": "PLAINTEXT"                  # and the value in the result
```

`no_log: true` censors a task that opts in, at any verbosity. It does **not**
protect a secret passed as an ordinary variable — the protection is per-task,
not global.

**Guard.** `install.py` rewrites both before anything reaches a file: the argv it
logs, and the `extra_vars:` line as it streams. Filtering our own argv is not
enough on its own; the value comes back a second time from Ansible.

Verify after any change to the streaming code:

```bash
appctl install myapp -- -e some_secret=PLAINTEXTVALUE
grep -rl PLAINTEXTVALUE tmp/          # must print nothing
```

### 3. In a rendered config

A rendered `config.json` is written under `tmp/rendered/`, which is gitignored —
but a secret in it would still be a secret on disk in plain text, and would be
copied to the guest.

**Guard.** The renderer substitutes `{{ name }}` and leaves `${NAME}` alone.
Collapsing the two would resolve a secret here and write it to a file. Keep them
distinct.

## Generating versus providing

`--generate` uses `secrets.token_urlsafe`, 32 bytes of entropy from the stdlib.
That is the right default for a token this system issues for itself.

It is wrong for anything with an external issuer. `example-app_provider_api_key` is
an Anthropic key: it has to come from Anthropic. `.env.example` says which is
which, based on the name.

## Checking what is required

`appctl secrets check` exits 1 while anything required is unset:

```
  example-app_provider_api_key    MISSING
  example-app_webui_token_secret  ok

1 of 4 required secrets are not set
  set them with: appctl secrets set <NAME> '<value>'
  or generate:   appctl secrets set <NAME> --generate
```

`appctl install` runs the same check before it touches the cluster and names the
missing keys. Discovering a missing secret halfway through a play leaves half the
guest configured.