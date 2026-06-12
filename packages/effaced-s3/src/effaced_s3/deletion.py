"""Batched ``delete_objects`` that keeps going past per-key failures.

Deletes the given identifiers in bounded batches (the object store's hard
cap per request), collecting each batch's per-key error codes. Batches
keep going past failures so every attempt makes monotonic progress —
retries then converge on the survivors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from effaced_s3.listing import DELETE_BATCH_SIZE

if TYPE_CHECKING:
    from types_boto3_s3.type_defs import ObjectIdentifierTypeDef

    from effaced_s3.object_client import S3ObjectClient


def delete_in_batches(
    client: S3ObjectClient,
    bucket: str,
    identifiers: list[ObjectIdentifierTypeDef],
) -> list[str]:
    """Delete every identifier in bounded batches; collect per-key error codes.

    Args:
        client: The object-store client to delete with.
        bucket: The bucket holding the subject's objects.
        identifiers: The (key, optional version) pairs to delete, ready
            for ``delete_objects`` batches.

    Returns:
        The per-key error codes the store reported, across all batches —
        empty when every deletion succeeded. Batches keep running past
        failures, so the codes accumulate without aborting the rest.
    """
    codes: list[str] = []
    for start in range(0, len(identifiers), DELETE_BATCH_SIZE):
        batch = identifiers[start : start + DELETE_BATCH_SIZE]
        response = client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
        codes.extend(item.get("Code", "") for item in response.get("Errors", []))
    return codes
