"""effaced-s3 — first-party S3 resolver for effaced."""

from importlib.metadata import PackageNotFoundError, version

from effaced_s3.partial_erase_error import PartialEraseError
from effaced_s3.resolver import S3Resolver

try:
    __version__ = version("effaced-s3")
except PackageNotFoundError:  # pragma: no cover - only hit on uninstalled source trees
    __version__ = "0.0.0"

__all__ = ["PartialEraseError", "S3Resolver", "__version__"]
