"""effaced-s3 — first-party S3 resolver for effaced.

The resolver itself is :class:`S3Resolver`. The object-store machinery it
rides on is public and stable, so S3-compatible stores (Supabase Storage,
MinIO, R2) can build their own resolvers on the same parts: the client
protocol :class:`S3ObjectClient`, the prefix guard :func:`checked_prefix`,
the export collector :func:`collect_object_records`, the listing helpers
:func:`iter_current_objects` and :func:`collect_version_identifiers`, the
batched delete :func:`delete_in_batches`, and the error taxonomy
(:func:`error_code`, :func:`is_nonretryable`, :data:`NONRETRYABLE_CODES`).
"""

from importlib.metadata import PackageNotFoundError, version

from effaced_s3.deletion import delete_in_batches
from effaced_s3.errors import NONRETRYABLE_CODES, error_code, is_nonretryable
from effaced_s3.export_collection import collect_object_records
from effaced_s3.listing import collect_version_identifiers, iter_current_objects
from effaced_s3.object_client import S3ObjectClient
from effaced_s3.partial_erase_error import PartialEraseError
from effaced_s3.prefix_guard import checked_prefix
from effaced_s3.resolver import S3Resolver

try:
    __version__ = version("effaced-s3")
except PackageNotFoundError:  # pragma: no cover - only hit on uninstalled source trees
    __version__ = "0.0.0"

__all__ = [
    "NONRETRYABLE_CODES",
    "PartialEraseError",
    "S3ObjectClient",
    "S3Resolver",
    "__version__",
    "checked_prefix",
    "collect_object_records",
    "collect_version_identifiers",
    "delete_in_batches",
    "error_code",
    "is_nonretryable",
    "iter_current_objects",
]
