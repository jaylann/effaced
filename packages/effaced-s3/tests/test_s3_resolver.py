"""S3-specific resolver behavior: mapping, pagination, partial failure, taxonomy."""

from __future__ import annotations

import asyncio
import base64

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fake_s3_client import FIXED_LAST_MODIFIED, FakeS3Client

from effaced import PiiCategory, Resolver, ResolverError, SubjectRef
from effaced_s3 import PartialEraseError, S3Resolver

PREFIX = "users/42/"
OTHER_PREFIX = "users/7/"
AVATAR_KEY = f"{PREFIX}avatar.png"
AVATAR_BODY = b"\x89PNG\r\n\x1a\nfake-binary-bytes\x00\xff"


def _ref(prefix: str = PREFIX) -> SubjectRef:
    return SubjectRef(kind="s3", value=prefix)


def _resolver(fake: FakeS3Client, **kwargs: object) -> S3Resolver:
    return S3Resolver(bucket="user-content", client=fake, **kwargs)


def _export(resolver: S3Resolver, prefix: str = PREFIX):
    return asyncio.run(resolver.export_subject(_ref(prefix)))


def _erase(resolver: S3Resolver, prefix: str = PREFIX):
    return asyncio.run(resolver.erase_subject(_ref(prefix)))


def _fields(export) -> dict[str, object]:
    return {record.field: record.value for record in export.records}


def _exported_keys(export) -> set[object]:
    return {
        record.value for record in export.records if record.field == f"object.{record.value}.key"
    }


class TestExportMapping:
    def test_full_field_mapping_with_binary_content(self) -> None:
        fake = FakeS3Client(
            objects={AVATAR_KEY: AVATAR_BODY},
            content_types={AVATAR_KEY: "image/png"},
            metadata={AVATAR_KEY: {"uploaded-by": "subject-42"}},
        )
        export = _export(_resolver(fake))
        fields = _fields(export)
        assert fields[f"object.{AVATAR_KEY}.key"] == AVATAR_KEY
        assert fields[f"object.{AVATAR_KEY}.size"] == len(AVATAR_BODY)
        assert fields[f"object.{AVATAR_KEY}.content_type"] == "image/png"
        assert fields[f"object.{AVATAR_KEY}.last_modified"] == FIXED_LAST_MODIFIED.isoformat()
        assert fields[f"object.{AVATAR_KEY}.metadata.uploaded-by"] == "subject-42"
        encoded = fields[f"object.{AVATAR_KEY}.content_base64"]
        assert isinstance(encoded, str)
        assert base64.b64decode(encoded) == AVATAR_BODY

    def test_categories_match_the_documented_table(self) -> None:
        fake = FakeS3Client(
            objects={AVATAR_KEY: AVATAR_BODY},
            metadata={AVATAR_KEY: {"origin": "app"}},
        )
        categories = {
            record.field.rsplit(".", 1)[-1] if ".metadata." not in record.field else "metadata": (
                record.category
            )
            for record in _export(_resolver(fake)).records
        }
        assert categories["key"] == PiiCategory.COMMUNICATION
        assert categories["content_base64"] == PiiCategory.COMMUNICATION
        assert categories["metadata"] == PiiCategory.COMMUNICATION
        assert categories["size"] == PiiCategory.TECHNICAL
        assert categories["content_type"] == PiiCategory.TECHNICAL
        assert categories["last_modified"] == PiiCategory.TECHNICAL

    def test_export_serializes_to_json_with_binary_content(self) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: bytes(range(256))})
        assert _export(_resolver(fake)).model_dump_json()

    def test_metadata_only_export_never_fetches_bodies(self) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY})
        export = _export(_resolver(fake, include_content=False))
        fields = _fields(export)
        assert f"object.{AVATAR_KEY}.content_base64" not in fields
        assert fields[f"object.{AVATAR_KEY}.size"] == len(AVATAR_BODY)
        assert "GetObject" not in {operation for operation, _ in fake.calls}

    def test_export_paginates_across_list_pages(self) -> None:
        objects = {f"{PREFIX}file-{index:02}.txt": b"x" for index in range(7)}
        fake = FakeS3Client(objects=dict(objects), page_size=3)
        export = _export(_resolver(fake))
        assert _exported_keys(export) == set(objects)

    def test_object_vanishing_between_list_and_get_is_skipped(self) -> None:
        vanishing = f"{PREFIX}gone.txt"
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY, vanishing: b"bye"})

        original = fake.get_object

        def vanishing_get(*, Bucket: str, Key: str):
            if Key == vanishing:
                fake._store.pop(vanishing, None)
            return original(Bucket=Bucket, Key=Key)

        fake.get_object = vanishing_get  # type: ignore[method-assign]
        export = _export(_resolver(fake))
        assert _exported_keys(export) == {AVATAR_KEY}

    def test_oversized_object_fails_loudly_without_naming_the_key(self) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY})
        with pytest.raises(ResolverError) as excinfo:
            _export(_resolver(fake, max_object_bytes=4))
        assert AVATAR_KEY not in str(excinfo.value)
        assert "GetObject" not in {operation for operation, _ in fake.calls}


class TestPrefixGuard:
    @pytest.mark.parametrize("prefix", [" ", "   ", "\t", "users/4", "users", "users/42/a.png"])
    def test_unsafe_prefix_is_rejected_before_any_call(self, prefix: str) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY})
        resolver = _resolver(fake)
        with pytest.raises(ResolverError):
            asyncio.run(resolver.export_subject(SubjectRef(kind="s3", value=prefix)))
        with pytest.raises(ResolverError):
            asyncio.run(resolver.erase_subject(SubjectRef(kind="s3", value=prefix)))
        assert fake.calls == []

    def test_sibling_stem_subjects_never_bleed(self) -> None:
        sibling = "users/421/avatar.png"
        fake = FakeS3Client(objects={"users/42/a.png": b"mine", sibling: b"not mine"})
        resolver = _resolver(fake)
        export = _export(resolver, "users/42/")
        assert _exported_keys(export) == {"users/42/a.png"}
        _erase(resolver, "users/42/")
        assert fake.stored_keys == {sibling}


class TestErasure:
    def test_erase_removes_all_versions_and_delete_markers(self) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: [b"v1", b"v2"]})
        fake.add_delete_marker(AVATAR_KEY)
        erasure = _erase(_resolver(fake))
        assert erasure.already_absent is False
        assert fake.stored_keys == set()
        assert "3" in (erasure.detail or "")

    def test_erase_works_on_unversioned_buckets(self) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY}, versioned=False)
        erasure = _erase(_resolver(fake))
        assert erasure.already_absent is False
        assert fake.stored_keys == set()

    def test_erase_touches_only_the_requested_prefix(self) -> None:
        bystander = f"{OTHER_PREFIX}avatar.png"
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY, bystander: b"keep me"})
        _erase(_resolver(fake))
        assert fake.stored_keys == {bystander}

    def test_large_version_sets_delete_in_bounded_batches(self) -> None:
        objects = {f"{PREFIX}f{index:04}": b"x" for index in range(1500)}
        fake = FakeS3Client(objects=dict(objects))
        _erase(_resolver(fake))
        batches = [
            len(kwargs["Delete"]["Objects"])
            for operation, kwargs in fake.calls
            if operation == "DeleteObjects"
        ]
        assert len(batches) >= 2
        assert all(size <= 1000 for size in batches)
        assert sum(batches) == 1500
        assert fake.stored_keys == set()

    def test_partial_transient_failure_raises_then_converges(self) -> None:
        failing = f"{PREFIX}stuck.bin"
        fake = FakeS3Client(
            objects={AVATAR_KEY: AVATAR_BODY, failing: b"flaky"},
            delete_errors={failing: "InternalError"},
        )
        resolver = _resolver(fake)
        with pytest.raises(PartialEraseError):
            _erase(resolver)
        assert AVATAR_KEY not in fake.stored_keys
        erasure = _erase(resolver)
        assert erasure.already_absent is False
        assert fake.stored_keys == set()

    def test_all_nonretryable_delete_errors_raise_resolver_error(self) -> None:
        fake = FakeS3Client(
            objects={AVATAR_KEY: AVATAR_BODY},
            delete_errors={AVATAR_KEY: "AccessDenied"},
        )
        with pytest.raises(ResolverError):
            _erase(_resolver(fake))

    def test_no_message_or_detail_ever_names_keys_or_prefixes(self) -> None:
        failing = f"{PREFIX}stuck.bin"
        fake = FakeS3Client(
            objects={AVATAR_KEY: AVATAR_BODY, failing: b"flaky"},
            delete_errors={failing: "InternalError"},
        )
        resolver = _resolver(fake)
        with pytest.raises(PartialEraseError) as excinfo:
            _erase(resolver)
        assert PREFIX not in str(excinfo.value)
        assert "stuck" not in str(excinfo.value)
        erasure = _erase(resolver)
        assert PREFIX not in (erasure.detail or "")


class TestErrorTaxonomy:
    @pytest.mark.parametrize("code", ["AccessDenied", "NoSuchBucket", "PermanentRedirect"])
    def test_nonretryable_codes_raise_resolver_error(self, code: str) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY}, error_code=code)
        resolver = _resolver(fake)
        with pytest.raises(ResolverError):
            _export(resolver)
        with pytest.raises(ResolverError):
            _erase(resolver)

    @pytest.mark.parametrize("code", ["SlowDown", "InternalError", "RequestTimeout"])
    def test_transient_codes_propagate_untranslated(self, code: str) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY}, error_code=code)
        resolver = _resolver(fake)
        with pytest.raises(ClientError):
            _export(resolver)
        with pytest.raises(ClientError):
            _erase(resolver)

    def test_connection_faults_propagate(self) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY}, connection_error=True)
        with pytest.raises(EndpointConnectionError):
            _export(_resolver(fake))

    def test_resolver_error_messages_carry_codes_not_identifiers(self) -> None:
        fake = FakeS3Client(objects={AVATAR_KEY: AVATAR_BODY}, error_code="AccessDenied")
        with pytest.raises(ResolverError) as excinfo:
            _export(_resolver(fake))
        message = str(excinfo.value)
        assert "AccessDenied" in message
        assert PREFIX not in message
        assert "user-content" not in message


class TestProtocol:
    def test_satisfies_resolver_protocol(self) -> None:
        assert isinstance(_resolver(FakeS3Client()), Resolver)

    def test_default_construction_builds_a_boto3_client(self) -> None:
        resolver = S3Resolver(bucket="user-content", region_name="eu-central-1")
        assert resolver.name == "s3"
