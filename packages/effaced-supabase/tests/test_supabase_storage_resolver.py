"""Supabase Storage resolver behaviour: mapping, no-versioning erasure, taxonomy."""

from __future__ import annotations

import asyncio
import base64

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fake_supabase_storage_client import FIXED_LAST_MODIFIED, FakeSupabaseStorageClient

from effaced import PiiCategory, Resolver, SubjectRef
from effaced.exceptions import ConfigurationError, ResolverError
from effaced_supabase.partial_storage_erase_error import PartialStorageEraseError
from effaced_supabase.storage_resolver import SupabaseStorageResolver

PREFIX = "users/42/"
OTHER_PREFIX = "users/7/"
AVATAR_KEY = f"{PREFIX}avatar.png"
AVATAR_BODY = b"\x89PNG\r\n\x1a\nfake-binary-bytes\x00\xff"

ENDPOINT = "https://proj.storage.supabase.co/storage/v1/s3"
PLACEHOLDER = "s"  # passed via a variable so S106 (hardcoded-password literal) stays quiet


def _ref(prefix: str = PREFIX) -> SubjectRef:
    return SubjectRef(kind="supabase_storage", value=prefix)


def _resolver(fake: FakeSupabaseStorageClient, **kwargs: object) -> SupabaseStorageResolver:
    return SupabaseStorageResolver(bucket="user-content", client=fake, **kwargs)


def _export(resolver: SupabaseStorageResolver, prefix: str = PREFIX):
    return asyncio.run(resolver.export_subject(_ref(prefix)))


def _erase(resolver: SupabaseStorageResolver, prefix: str = PREFIX):
    return asyncio.run(resolver.erase_subject(_ref(prefix)))


def _fields(export) -> dict[str, object]:
    return {record.field: record.value for record in export.records}


def _exported_keys(export) -> set[object]:
    return {
        record.value for record in export.records if record.field == f"object.{record.value}.key"
    }


def _ops(fake: FakeSupabaseStorageClient) -> set[str]:
    return {operation for operation, _ in fake.calls}


class TestConstruction:
    def test_missing_connection_param_raises_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError):
            SupabaseStorageResolver(
                bucket="b", endpoint_url=ENDPOINT, access_key_id="k", secret_access_key=None
            )
        with pytest.raises(ConfigurationError):
            SupabaseStorageResolver(
                bucket="b", endpoint_url=ENDPOINT, access_key_id="k", secret_access_key=PLACEHOLDER
            )
        with pytest.raises(ConfigurationError):
            SupabaseStorageResolver(bucket="b")

    def test_explicit_client_needs_no_connection_params(self) -> None:
        resolver = _resolver(FakeSupabaseStorageClient())
        assert resolver.name == "supabase_storage"


class TestExportMapping:
    def test_full_field_mapping_with_binary_content(self) -> None:
        fake = FakeSupabaseStorageClient(
            objects={AVATAR_KEY: AVATAR_BODY},
            content_types={AVATAR_KEY: "image/png"},
            metadata={AVATAR_KEY: {"uploaded-by": "subject-42"}},
        )
        fields = _fields(_export(_resolver(fake)))
        assert fields[f"object.{AVATAR_KEY}.key"] == AVATAR_KEY
        assert fields[f"object.{AVATAR_KEY}.size"] == len(AVATAR_BODY)
        assert fields[f"object.{AVATAR_KEY}.content_type"] == "image/png"
        assert fields[f"object.{AVATAR_KEY}.last_modified"] == FIXED_LAST_MODIFIED.isoformat()
        assert fields[f"object.{AVATAR_KEY}.metadata.uploaded-by"] == "subject-42"
        encoded = fields[f"object.{AVATAR_KEY}.content_base64"]
        assert isinstance(encoded, str)
        assert base64.b64decode(encoded) == AVATAR_BODY

    def test_records_are_sourced_to_supabase_storage(self) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY})
        export = _export(_resolver(fake))
        assert export.resolver == "supabase_storage"
        assert {record.source for record in export.records} == {"supabase_storage"}

    def test_key_and_content_are_communication_size_is_technical(self) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY})
        by_field = {record.field: record.category for record in _export(_resolver(fake)).records}
        assert by_field[f"object.{AVATAR_KEY}.key"] == PiiCategory.COMMUNICATION
        assert by_field[f"object.{AVATAR_KEY}.content_base64"] == PiiCategory.COMMUNICATION
        assert by_field[f"object.{AVATAR_KEY}.size"] == PiiCategory.TECHNICAL

    def test_metadata_only_export_uses_head_not_get(self) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY})
        fields = _fields(_export(_resolver(fake, include_content=False)))
        assert f"object.{AVATAR_KEY}.content_base64" not in fields
        assert fields[f"object.{AVATAR_KEY}.size"] == len(AVATAR_BODY)
        assert "GetObject" not in _ops(fake)
        assert "HeadObject" in _ops(fake)

    def test_export_paginates_across_list_pages(self) -> None:
        objects = {f"{PREFIX}file-{index:02}.txt": b"x" for index in range(7)}
        fake = FakeSupabaseStorageClient(objects=dict(objects), page_size=3)
        assert _exported_keys(_export(_resolver(fake))) == set(objects)

    def test_oversized_object_fails_loudly_without_naming_the_key(self) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY})
        with pytest.raises(ResolverError) as excinfo:
            _export(_resolver(fake, max_object_bytes=4))
        assert AVATAR_KEY not in str(excinfo.value)
        assert "GetObject" not in _ops(fake)


class TestPrefixGuard:
    @pytest.mark.parametrize("prefix", [" ", "   ", "\t", "users/4", "users", "users/42/a.png"])
    def test_unsafe_prefix_is_rejected_before_any_call(self, prefix: str) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY})
        resolver = _resolver(fake)
        with pytest.raises(ResolverError):
            asyncio.run(resolver.export_subject(SubjectRef(kind="supabase_storage", value=prefix)))
        with pytest.raises(ResolverError):
            asyncio.run(resolver.erase_subject(SubjectRef(kind="supabase_storage", value=prefix)))
        assert fake.calls == []

    def test_sibling_stem_subjects_never_bleed(self) -> None:
        sibling = "users/421/avatar.png"
        fake = FakeSupabaseStorageClient(objects={"users/42/a.png": b"mine", sibling: b"not mine"})
        resolver = _resolver(fake)
        assert _exported_keys(_export(resolver, "users/42/")) == {"users/42/a.png"}
        _erase(resolver, "users/42/")
        assert fake.stored_keys == {sibling}


class TestErasure:
    def test_erase_deletes_current_objects(self) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY, f"{PREFIX}b": b"x"})
        erasure = _erase(_resolver(fake))
        assert erasure.already_absent is False
        assert fake.stored_keys == set()
        assert erasure.detail == "deleted 2 objects"

    def test_erase_never_calls_list_object_versions(self) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY})
        _erase(_resolver(fake))
        assert "ListObjectVersions" not in _ops(fake)

    def test_empty_prefix_listing_is_already_absent(self) -> None:
        fake = FakeSupabaseStorageClient(objects={f"{OTHER_PREFIX}x": b"y"})
        erasure = _erase(_resolver(fake))
        assert erasure.already_absent is True
        assert erasure.detail == "nothing held under the prefix"

    def test_erase_touches_only_the_requested_prefix(self) -> None:
        bystander = f"{OTHER_PREFIX}avatar.png"
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY, bystander: b"keep me"})
        _erase(_resolver(fake))
        assert fake.stored_keys == {bystander}

    def test_mixed_delete_errors_raise_partial_and_still_delete_the_rest(self) -> None:
        failing = f"{PREFIX}stuck.bin"
        fake = FakeSupabaseStorageClient(
            objects={AVATAR_KEY: AVATAR_BODY, failing: b"flaky"},
            delete_errors={failing: "InternalError"},
        )
        resolver = _resolver(fake)
        with pytest.raises(PartialStorageEraseError):
            _erase(resolver)
        assert AVATAR_KEY not in fake.stored_keys
        erasure = _erase(resolver)
        assert erasure.already_absent is False
        assert fake.stored_keys == set()

    def test_all_nonretryable_delete_errors_raise_resolver_error(self) -> None:
        fake = FakeSupabaseStorageClient(
            objects={AVATAR_KEY: AVATAR_BODY},
            delete_errors={AVATAR_KEY: "AccessDenied"},
        )
        with pytest.raises(ResolverError):
            _erase(_resolver(fake))

    def test_no_message_or_detail_ever_names_keys_or_prefixes(self) -> None:
        failing = f"{PREFIX}stuck.bin"
        fake = FakeSupabaseStorageClient(
            objects={AVATAR_KEY: AVATAR_BODY, failing: b"flaky"},
            delete_errors={failing: "InternalError"},
        )
        resolver = _resolver(fake)
        with pytest.raises(PartialStorageEraseError) as excinfo:
            _erase(resolver)
        assert PREFIX not in str(excinfo.value)
        assert "stuck" not in str(excinfo.value)
        erasure = _erase(resolver)
        assert PREFIX not in (erasure.detail or "")


class TestErrorTaxonomy:
    @pytest.mark.parametrize("code", ["AccessDenied", "NoSuchBucket", "InvalidAccessKeyId"])
    def test_nonretryable_codes_raise_resolver_error(self, code: str) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY}, error_code=code)
        resolver = _resolver(fake)
        with pytest.raises(ResolverError):
            _export(resolver)
        with pytest.raises(ResolverError):
            _erase(resolver)

    @pytest.mark.parametrize("code", ["SlowDown", "InternalError", "RequestTimeout"])
    def test_transient_codes_propagate_untranslated(self, code: str) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY}, error_code=code)
        resolver = _resolver(fake)
        with pytest.raises(ClientError):
            _export(resolver)
        with pytest.raises(ClientError):
            _erase(resolver)

    def test_connection_faults_propagate(self) -> None:
        fake = FakeSupabaseStorageClient(objects={AVATAR_KEY: AVATAR_BODY}, connection_error=True)
        with pytest.raises(EndpointConnectionError):
            _export(_resolver(fake))
        with pytest.raises(EndpointConnectionError):
            _erase(_resolver(fake))

    def test_resolver_error_messages_carry_codes_not_identifiers(self) -> None:
        fake = FakeSupabaseStorageClient(
            objects={AVATAR_KEY: AVATAR_BODY}, error_code="AccessDenied"
        )
        with pytest.raises(ResolverError) as excinfo:
            _export(_resolver(fake))
        message = str(excinfo.value)
        assert "AccessDenied" in message
        assert PREFIX not in message
        assert "user-content" not in message


class TestProtocol:
    def test_satisfies_resolver_protocol(self) -> None:
        assert isinstance(_resolver(FakeSupabaseStorageClient()), Resolver)
