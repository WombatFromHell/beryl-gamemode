"""Version information for Gamemode."""

from importlib.metadata import PackageNotFoundError, version

# This value is replaced at build time by the Makefile
__version__ = "1.0.2"


def _get_version() -> str:
    """Get version from embedded value or package metadata fallback."""
    if __version__ != "DEV":
        return __version__
    try:
        return version("gamemode")
    except PackageNotFoundError:
        return "unknown"
