# DeployFlow documentation

| | |
|---|---|
| [overview.md](overview.md) | the current implementation: the command, the target, the roles |
| [architecture.md](architecture.md) | how it fits together, and the one rule |
| [targets.md](targets.md) | the delivery-target interface, and how to add a target |
| [configuration.md](configuration.md) | every key in `app.yaml` |
| [rendering.md](rendering.md) | YAML → config on the guest |
| [secrets.md](secrets.md) | `.env`, and the three ways a secret leaks |
| [build-pipeline.md](build-pipeline.md) | wheel and lockfile from a checkout |

Start with [overview.md](overview.md) for the current implementation and
[architecture.md](architecture.md) for the rules that hold it together. For what
the workflow is *for*, read the [project README](../README.md); for the rules
on changing it, [../AGENTS.md](../AGENTS.md).
