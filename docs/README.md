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
| [diagrams/deployflow-workflow.html](diagrams/deployflow-workflow.html) | the workflow, lane by lane: who does what, and where the proof happens |
| [diagrams/deployflow-anatomy.html](diagrams/deployflow-anatomy.html) | what an application looks like in the repository, and which file holds each part |
| [diagrams/deployflow-current-state.html](diagrams/deployflow-current-state.html) | the current landscape: what is built, what is named but not built, and where the proof lives |
| [../apps/omniroute/](../apps/omniroute/) | a working application built from a source checkout |
| [../apps/builder/](../apps/builder/) | a working build host — the `toolchain` application |

Start with [overview.md](overview.md) for the current implementation and
[architecture.md](architecture.md) for the rules that hold it together. For what
the workflow is *for*, read the [project README](../README.md); for the rules
on changing it, [../AGENTS.md](../AGENTS.md).
