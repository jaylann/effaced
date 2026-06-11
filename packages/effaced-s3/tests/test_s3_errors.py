"""The S3 error taxonomy: which ClientErrors abandon and which retry."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from effaced_s3.errors import is_nonretryable

NONRETRYABLE_CODES = [
    "AccessDenied",
    "AllAccessDisabled",
    "AccountProblem",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "InvalidBucketName",
    "NoSuchBucket",
    "PermanentRedirect",
]

TRANSIENT_CODES = [
    "SlowDown",
    "Throttling",
    "RequestTimeout",
    "InternalError",
    "ServiceUnavailable",
    "ExpiredToken",
    "SomeCodeInventedTomorrow",
]


def _client_error(code: str | None, status: int = 400) -> ClientError:
    error: dict[str, str] = {} if code is None else {"Code": code}
    return ClientError(
        {"Error": error, "ResponseMetadata": {"HTTPStatusCode": status}},
        "ListObjectVersions",
    )


@pytest.mark.parametrize("code", NONRETRYABLE_CODES)
def test_nonretryable_codes(code: str) -> None:
    assert is_nonretryable(_client_error(code)) is True


@pytest.mark.parametrize("code", TRANSIENT_CODES)
def test_transient_and_unknown_codes_default_to_retry(code: str) -> None:
    assert is_nonretryable(_client_error(code)) is False


def test_moved_permanently_status_without_a_code_is_nonretryable() -> None:
    assert is_nonretryable(_client_error(None, status=301)) is True


def test_plain_400_without_a_known_code_retries() -> None:
    assert is_nonretryable(_client_error(None, status=400)) is False
