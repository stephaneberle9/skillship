"""skillship: resolve, lock and deliver curated subsets ("distros") of Agent Skills."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("skillship")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"
