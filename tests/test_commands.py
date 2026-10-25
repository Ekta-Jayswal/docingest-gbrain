# What this file does: tests how the brain folder is chosen (--brain, .env.local, the default
# ./brain, or a value already in the shell) and how a setting is read from .env.local.

"""Choosing the brain folder, and reading a setting from .env.local."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from docingest import commands

# The project root, one level above the docingest package.
ROOT = Path(commands.__file__).resolve().parents[1]


# Checks for reading one setting from a .env.local file.
class EnvLocalValueTest(unittest.TestCase):
    """Reading one setting from a .env.local file."""

    # Set up a temporary folder holding the .env.local file.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".env.local"

    def tearDown(self):
        self.tmp.cleanup()

    # A plain line gives its value, without the spaces around it.
    def test_a_plain_line_gives_its_value(self):
        self.path.write_text("GBRAIN_HOME=/data/brain \n")
        self.assertEqual(commands._env_local_value("GBRAIN_HOME", self.path), "/data/brain")

    # A commented-out line does not count as a setting.
    def test_a_commented_out_line_is_missing(self):
        self.path.write_text("#GBRAIN_HOME=/data/brain\n")
        self.assertIsNone(commands._env_local_value("GBRAIN_HOME", self.path))

    # A line with nothing after the equals sign gives None.
    def test_an_empty_value_is_none(self):
        self.path.write_text("GBRAIN_HOME=\n")
        self.assertIsNone(commands._env_local_value("GBRAIN_HOME", self.path))

    # A missing file gives None rather than an error.
    def test_a_missing_file_is_none(self):
        self.assertIsNone(commands._env_local_value("GBRAIN_HOME", Path(self.tmp.name) / "none"))


# Checks for which brain folder is used, and where the value came from.
class BrainHomeTest(unittest.TestCase):
    """The brain folder and its source, for each place it can be set."""

    # Run _brain_home with the given --brain value, .env.local value and shell value. The shell
    # starts without GBRAIN_HOME, and the environment is restored afterwards.
    def brain_home(self, brain=None, local=None, shell=None):
        args = SimpleNamespace(brain=brain)
        with mock.patch.dict(os.environ):
            os.environ.pop("GBRAIN_HOME", None)
            if shell is not None:
                os.environ["GBRAIN_HOME"] = shell
            with mock.patch.object(commands, "_env_local_value", return_value=local):
                return commands._brain_home(args)

    # The --brain option wins over the other places.
    def test_the_brain_option_wins(self):
        self.assertEqual(
            self.brain_home(brain="/a", local="/b", shell="/c"), ("/a", "--brain option")
        )

    # .env.local is used when there is no --brain option, even if the shell has a value.
    def test_env_local_is_used_before_the_shell(self):
        self.assertEqual(self.brain_home(local="/b", shell="/c"), ("/b", ".env.local"))

    # A value only in the shell is used, and the source says so.
    def test_a_shell_value_is_reported_as_such(self):
        self.assertEqual(
            self.brain_home(shell="/c"), ("/c", "shell environment, not .env.local")
        )

    # The folder ingest.sh sets by default is reported as the default ./brain.
    def test_the_default_brain_folder_is_reported_as_default(self):
        default = str(ROOT / "brain")
        self.assertEqual(self.brain_home(shell=default), (default, "default ./brain"))

    # With nothing set anywhere, there is no folder.
    def test_nothing_set_gives_none(self):
        self.assertEqual(self.brain_home(), (None, "not set"))


if __name__ == "__main__":
    unittest.main()
