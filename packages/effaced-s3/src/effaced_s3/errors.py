"""S3 error taxonomy — which failures retry and which abandon.

The saga runner retries any exception that is not a
:class:`~effaced.exceptions.ResolverError`, so the resolver classifies
``botocore`` failures as follows:

==================================  =========================================
S3 failure                          Treatment
==================================  =========================================
``ClientError`` with a code in      :class:`ResolverError` — bad or revoked
:data:`NONRETRYABLE_CODES` (or      credentials, a missing permission, a
HTTP 301: wrong-region endpoint)    missing bucket, or a wrong endpoint; the
                                    same request can never succeed on retry.
``NoSuchKey`` / ``404`` on get or   The object vanished between list and
head                                fetch: it is skipped — the system no
                                    longer holds it.
``delete_objects`` per-key errors,  :class:`ResolverError` when every code is
                                    non-retryable; otherwise
                                    :class:`~effaced_s3.PartialEraseError`,
                                    which propagates so the saga retries.
``SlowDown`` / ``Throttling`` /     Propagates — saga backoff is the cure.
5xx / **any unknown code**          Unknown codes default to transient
                                    because abandonment is permanent and
                                    loud; retrying is the safe failure mode.
``EndpointConnectionError`` and     Propagates — network blip.
other ``BotoCoreError`` types
==================================  =========================================
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from botocore.exceptions import ClientError

NONRETRYABLE_CODES = frozenset(
    {
        "AccessDenied",
        "AllAccessDisabled",
        "AccountProblem",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidBucketName",
        "NoSuchBucket",
        "PermanentRedirect",
    }
)
"""Error codes that can never succeed on retry — they abandon immediately."""

_MOVED_PERMANENTLY = 301

_ABSENT_OBJECT_CODES = frozenset({"NoSuchKey", "NotFound", "404"})


def error_code(error: ClientError) -> str:
    """The S3 error code of a ``ClientError``, or ``""`` when absent.

    Args:
        error: The ``ClientError`` botocore raised.

    Returns:
        The ``Error.Code`` field of the error response body.
    """
    code = error.response.get("Error", {}).get("Code", "")
    return code if isinstance(code, str) else ""


def is_nonretryable(error: ClientError) -> bool:
    """Whether a ``ClientError`` should abandon instead of retry.

    Args:
        error: The ``ClientError`` botocore raised.

    Returns:
        True for credential, permission, missing-bucket, and
        wrong-endpoint failures; False for everything else — throttling,
        server faults, and codes this taxonomy does not know.
    """
    if error_code(error) in NONRETRYABLE_CODES:
        return True
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == _MOVED_PERMANENTLY


def is_absent_object(error: ClientError) -> bool:
    """Whether S3 answered "no such object" — the vanished-object case.

    Args:
        error: The ``ClientError`` a get or head call raised.

    Returns:
        True when the request failed only because the object does not
        exist (``NoSuchKey``, or the bare ``404`` HEAD responses carry).
    """
    return error_code(error) in _ABSENT_OBJECT_CODES
