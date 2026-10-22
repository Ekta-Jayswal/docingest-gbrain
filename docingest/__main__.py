# What this file does: lets you run the tool with `python -m docingest`.
# It only hands control to the command-line handler in commands.py.

"""Entry point for ``python -m docingest``."""

import sys

from docingest.commands import main

sys.exit(main())
