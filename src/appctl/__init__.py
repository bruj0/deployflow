"""appctl — build, install and operate this repository's application containers.

Three commands, matching the three jobs:

    appctl build <app>      produce the artifacts a role installs
    appctl install         run the Ansible play, streaming and logging
    appctl secrets ...     manage the .env the play reads

The package deliberately knows nothing about any particular application. It
reads app.yaml at the repository root and acts on what it finds there, so an
application is added by editing configuration, not by editing this code.
"""

__version__ = "0.1.0"
