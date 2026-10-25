"""The application example-app serves.

Short on purpose: everything else in this role is the part worth reading.

`app` is the WSGI entry point the server is started with. The name of the
module is what the role's import check runs, and it is a valid Python
identifier — the application's own name (`example-app`) contains a hyphen and
cannot be imported.
"""

import json
import os

from bottle import Bottle

# The state directory, not the install root. The configuration appctl rendered
# on the controller lands in it, and reading it back is what proves the config
# path works rather than just the service.
STATE_DIR = os.environ.get("APP_STATE_DIR", "/opt/example-app/state")

app = Bottle()


@app.route("/")
def index():
    """Report what the application was configured with.

    The port comes from the configuration, not from a constant. A service that
    answers with a hardcoded value proves it is running; one that answers with
    the configured value proves it is running correctly.
    """
    port = None
    path = os.path.join(STATE_DIR, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            port = json.load(handle).get("server", {}).get("port")
    return {"application": "example-app", "configured_port": port}


@app.route("/healthz")
def healthz():
    """The endpoint the role's own check asks for."""
    return {"status": "ok"}
