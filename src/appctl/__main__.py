"""Entry point for `python -m lxc`, which is how the tests drive the tool."""

import sys

from .cli import main

sys.exit(main())
