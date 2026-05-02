"""Poker CLI - AI security agent CLI."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("poker-cli")
except PackageNotFoundError:  # editable install / source-tree run
    __version__ = "dev"
