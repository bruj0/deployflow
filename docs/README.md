# DeployFlow documentation

| | |
|---|---|
| [overview.md](overview.md) | the current implementation: the command, the target, the roles |
| [architecture.md](architecture.md) | how it fits together, and the one rule |
| [targets.md](targets.md) | the delivery-target interface, and how to add a target |
| [configuration.md](configuration.md) | every key in `app.yaml` |
| [rendering.md](rendering.md) | YAML → config on the guest |
| [secrets.md](secrets.md) | `.env`, and the three ways a secret leaks |
| [build-pipeline.md](build-pipeline.md) | the two build paths: a wheel from a checkout, and a tree from a build host |
| [../apps/omniroute/](../apps/omniroute/) | a working application built from a source checkout |
| [../apps/builder/](../apps/builder/) | a working build host — the `toolchain` application |

Start with [overview.md](overview.md) for the current implementation and
[architecture.md](architecture.md) for the rules that hold it together. For what
the workflow is *for*, read the [project README](../README.md); for the rules
on changing it, [../AGENTS.md](../AGENTS.md).
