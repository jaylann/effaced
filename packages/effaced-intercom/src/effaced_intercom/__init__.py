"""effaced-intercom — the first-party Intercom resolver for effaced."""

from importlib.metadata import PackageNotFoundError, version

from effaced_intercom.resolver import IntercomResolver

try:
    __version__ = version("effaced-intercom")
except PackageNotFoundError:  # pragma: no cover - only hit on uninstalled source trees
    __version__ = "0.0.0"

__all__ = ["IntercomResolver", "__version__"]
