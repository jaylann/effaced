"""effaced-resend — the first-party Resend resolver for effaced."""

from importlib.metadata import PackageNotFoundError, version

from effaced_resend.resolver import ResendResolver

try:
    __version__ = version("effaced-resend")
except PackageNotFoundError:  # pragma: no cover - only hit on uninstalled source trees
    __version__ = "0.0.0"

__all__ = ["ResendResolver", "__version__"]
