"""
Tests for oci_storage — lazy client construction, object reads, prefix listing.

Everything here is mocked. The suite must pass on a laptop or in CI with no
instance-metadata endpoint, which is the whole point of the laziness change:
importing this module used to require being on an OCI instance.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import oci
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import oci_storage  # noqa: E402


# --- fakes -----------------------------------------------------------------

class FakeBody:
    def __init__(self, content: bytes):
        self.content = content


class FakeStreamBody:
    """A body exposing only .raw.read(), to cover the non-.content branch."""

    class _Raw:
        def __init__(self, content):
            self._c = content

        def read(self):
            return self._c

    def __init__(self, content: bytes):
        self.raw = self._Raw(content)


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeSummary:
    def __init__(self, name, size=0, time_modified=None):
        self.name = name
        self.size = size
        self.time_modified = time_modified


class FakePage:
    def __init__(self, objects, next_start_with=None):
        self.objects = objects
        self.next_start_with = next_start_with


class FakeClient:
    """Records calls and replays scripted pages/objects."""

    def __init__(self, pages=None, objects=None):
        self._pages = list(pages or [])
        self._objects = dict(objects or {})
        self.list_calls = []
        self.put_calls = []
        self.head_calls = []

    def get_namespace(self):
        return FakeResponse("fake-namespace")

    def list_objects(self, namespace, bucket, **kwargs):
        self.list_calls.append((namespace, bucket, kwargs))
        return FakeResponse(self._pages.pop(0))

    def get_object(self, namespace, bucket, object_name):
        if object_name not in self._objects:
            raise oci.exceptions.ServiceError(404, "ObjectNotFound", {}, "nope")
        return FakeResponse(FakeBody(self._objects[object_name]))

    def head_object(self, namespace, bucket, object_name):
        self.head_calls.append(object_name)
        if object_name not in self._objects:
            raise oci.exceptions.ServiceError(404, "ObjectNotFound", {}, "nope")
        return FakeResponse(None)

    def put_object(self, namespace, bucket, object_name, body, content_type=None):
        self.put_calls.append((bucket, object_name, body, content_type))
        return FakeResponse(None)


@pytest.fixture
def storage(monkeypatch):
    """oci_storage with a fresh, injected fake client. State is auto-restored."""
    monkeypatch.setattr(oci_storage, "_client", None, raising=False)
    monkeypatch.setattr(oci_storage, "_namespace", "fake-namespace", raising=False)
    return oci_storage


def _inject(monkeypatch, client):
    monkeypatch.setattr(oci_storage, "_client", client, raising=False)
    monkeypatch.setattr(oci_storage, "_namespace", "fake-namespace", raising=False)
    return client


# --- laziness --------------------------------------------------------------

class TestLazyClient:

    def test_import_does_not_build_a_signer(self, monkeypatch):
        """
        The regression this change exists to prevent: importing oci_storage
        must not touch instance metadata.
        """
        def explode(*a, **kw):
            raise RuntimeError("instance metadata unreachable")

        monkeypatch.setattr(
            oci.auth.signers, "InstancePrincipalsSecurityTokenSigner", explode
        )
        monkeypatch.delitem(sys.modules, "oci_storage", raising=False)

        module = importlib.import_module("oci_storage")   # must not raise
        assert module._client is None
        assert module._namespace is None

        # ...and the failure surfaces on use, as a typed error
        with pytest.raises(module.OciUnavailableError):
            module._get_client()

    def test_client_is_built_once_and_cached(self, monkeypatch):
        builds = []

        def signer(*a, **kw):
            builds.append(1)
            return object()

        monkeypatch.setattr(oci.auth.signers, "InstancePrincipalsSecurityTokenSigner", signer)
        monkeypatch.setattr(
            oci.object_storage, "ObjectStorageClient", lambda cfg, signer=None: FakeClient()
        )
        monkeypatch.setattr(oci_storage, "_client", None, raising=False)
        monkeypatch.setattr(oci_storage, "_namespace", "ns", raising=False)

        first = oci_storage._get_client()
        second = oci_storage._get_client()
        assert first is second
        assert len(builds) == 1

    def test_is_available_is_false_rather_than_raising(self, monkeypatch):
        def explode(*a, **kw):
            raise RuntimeError("no metadata")

        monkeypatch.setattr(oci.auth.signers, "InstancePrincipalsSecurityTokenSigner", explode)
        monkeypatch.setattr(oci_storage, "_client", None, raising=False)
        monkeypatch.setattr(oci_storage, "_namespace", None, raising=False)
        assert oci_storage.is_available() is False

    def test_configured_namespace_avoids_client_construction(self, monkeypatch):
        """With OCI_NAMESPACE set, URL building needs no credentials at all."""
        def explode(*a, **kw):
            raise AssertionError("client must not be built")

        monkeypatch.setattr(oci.auth.signers, "InstancePrincipalsSecurityTokenSigner", explode)
        monkeypatch.setattr(oci_storage, "_client", None, raising=False)
        monkeypatch.setattr(oci_storage, "_namespace", None, raising=False)
        monkeypatch.setattr(oci_storage, "_cfg", lambda n, f: "cfg-ns" if n == "OCI_NAMESPACE" else f)

        assert oci_storage._get_namespace() == "cfg-ns"
        assert oci_storage._client is None


# --- URLs ------------------------------------------------------------------

class TestPublicUrl:

    def test_default_bucket(self, storage):
        url = storage.get_public_url("dir/file.html")
        assert "/n/fake-namespace/" in url
        assert f"/b/{storage.BUCKET_NAME}/" in url
        assert url.endswith("/o/dir/file.html")

    def test_bucket_override(self, storage):
        url = storage.get_public_url("a.html", bucket=storage.HTML_BUCKET_NAME)
        assert f"/b/{storage.HTML_BUCKET_NAME}/" in url

    def test_special_characters_are_encoded_but_slashes_survive(self, storage):
        url = storage.get_public_url("09111944_Chap 5 — 79-99/x.html")
        assert "%20" in url and "%E2%80%94" in url
        assert url.count("/o/") == 1
        assert "/o/09111944_Chap" in url


# --- reads -----------------------------------------------------------------

class TestGetObject:

    def test_returns_bytes(self, monkeypatch):
        _inject(monkeypatch, FakeClient(objects={"a/b.json": b'{"k":1}'}))
        assert oci_storage.get_object("a/b.json") == b'{"k":1}'

    def test_missing_object_returns_none(self, monkeypatch):
        _inject(monkeypatch, FakeClient(objects={}))
        assert oci_storage.get_object("nope.json") is None

    def test_non_404_service_error_propagates(self, monkeypatch):
        class Boom(FakeClient):
            def get_object(self, *a, **kw):
                raise oci.exceptions.ServiceError(403, "NotAuthorized", {}, "denied")

        _inject(monkeypatch, Boom())
        with pytest.raises(oci.exceptions.ServiceError) as err:
            oci_storage.get_object("x")
        assert err.value.status == 403

    def test_stream_only_body_is_read(self, monkeypatch):
        class Streaming(FakeClient):
            def get_object(self, *a, **kw):
                return FakeResponse(FakeStreamBody(b"streamed"))

        _inject(monkeypatch, Streaming())
        assert oci_storage.get_object("x") == b"streamed"

    def test_get_text_decodes(self, monkeypatch):
        _inject(monkeypatch, FakeClient(objects={"h.html": "अभ्यास".encode("utf-8")}))
        assert oci_storage.get_text("h.html") == "अभ्यास"

    def test_get_text_missing_is_none(self, monkeypatch):
        _inject(monkeypatch, FakeClient(objects={}))
        assert oci_storage.get_text("missing") is None


class TestObjectExists:

    def test_true_and_false(self, monkeypatch):
        _inject(monkeypatch, FakeClient(objects={"there": b"x"}))
        assert oci_storage.object_exists("there") is True
        assert oci_storage.object_exists("absent") is False

    def test_non_404_propagates(self, monkeypatch):
        class Boom(FakeClient):
            def head_object(self, *a, **kw):
                raise oci.exceptions.ServiceError(500, "Internal", {}, "boom")

        _inject(monkeypatch, Boom())
        with pytest.raises(oci.exceptions.ServiceError):
            oci_storage.object_exists("x")


# --- listing ---------------------------------------------------------------

class TestListing:

    def test_single_page(self, monkeypatch):
        client = _inject(monkeypatch, FakeClient(pages=[
            FakePage([FakeSummary("a", 1), FakeSummary("b", 2)]),
        ]))
        got = oci_storage.list_objects(prefix="p/")
        assert [g["name"] for g in got] == ["a", "b"]
        assert [g["size"] for g in got] == [1, 2]
        assert client.list_calls[0][2]["prefix"] == "p/"

    def test_paginates_until_next_start_with_is_empty(self, monkeypatch):
        client = _inject(monkeypatch, FakeClient(pages=[
            FakePage([FakeSummary("a")], next_start_with="a"),
            FakePage([FakeSummary("b")], next_start_with="b"),
            FakePage([FakeSummary("c")], next_start_with=None),
        ]))
        got = oci_storage.list_objects()
        assert [g["name"] for g in got] == ["a", "b", "c"]
        assert len(client.list_calls) == 3
        # first request carries no cursor, later ones do
        assert "start" not in client.list_calls[0][2]
        assert client.list_calls[1][2]["start"] == "a"
        assert client.list_calls[2][2]["start"] == "b"

    def test_limit_stops_early_without_fetching_more_pages(self, monkeypatch):
        client = _inject(monkeypatch, FakeClient(pages=[
            FakePage([FakeSummary("a"), FakeSummary("b"), FakeSummary("c")],
                     next_start_with="c"),
            FakePage([FakeSummary("d")]),
        ]))
        got = oci_storage.list_objects(limit=2)
        assert [g["name"] for g in got] == ["a", "b"]
        assert len(client.list_calls) == 1

    def test_empty_page_terminates(self, monkeypatch):
        _inject(monkeypatch, FakeClient(pages=[FakePage(None)]))
        assert oci_storage.list_objects() == []

    def test_iter_objects_is_lazy(self, monkeypatch):
        client = _inject(monkeypatch, FakeClient(pages=[
            FakePage([FakeSummary("a")], next_start_with="a"),
            FakePage([FakeSummary("b")]),
        ]))
        first = next(oci_storage.iter_objects())
        assert first["name"] == "a"
        assert len(client.list_calls) == 1   # second page not requested yet

    def test_requests_page_size_and_fields(self, monkeypatch):
        client = _inject(monkeypatch, FakeClient(pages=[FakePage([])]))
        oci_storage.list_objects()
        kwargs = client.list_calls[0][2]
        assert kwargs["limit"] == oci_storage._LIST_PAGE_SIZE
        assert "name" in kwargs["fields"] and "size" in kwargs["fields"]

    def test_bucket_override(self, monkeypatch):
        client = _inject(monkeypatch, FakeClient(pages=[FakePage([])]))
        oci_storage.list_objects(bucket="other-bucket")
        assert client.list_calls[0][1] == "other-bucket"


# --- writes ----------------------------------------------------------------

class TestUploadBytes:

    def test_uploads_and_returns_url(self, monkeypatch):
        client = _inject(monkeypatch, FakeClient())
        url = oci_storage.upload_bytes(
            b'{"nodes":[]}', "graph/v1/index.json",
            bucket=oci_storage.HTML_BUCKET_NAME,
            content_type="application/json",
        )
        bucket, name, body, ctype = client.put_calls[0]
        assert bucket == oci_storage.HTML_BUCKET_NAME
        assert name == "graph/v1/index.json"
        assert body == b'{"nodes":[]}'
        assert ctype == "application/json"
        assert url.endswith("/o/graph/v1/index.json")

    def test_json_sidecars_bypass_the_upload_directory_filter(self, monkeypatch):
        """
        upload_directory deliberately skips .json; upload_bytes is the sanctioned
        way to publish a JSON sidecar. Pinned so the two stay reconciled.
        """
        client = _inject(monkeypatch, FakeClient())
        oci_storage.upload_bytes(b"{}", "graph/v1/chapters/x.json")
        assert client.put_calls[0][1].endswith(".json")


# --- config wiring ---------------------------------------------------------

class TestConfigDefaults:
    """Defaults must equal the values that were previously hardcoded."""

    def test_bucket_and_region_defaults_unchanged(self):
        assert oci_storage.REGION == "ap-hyderabad-1"
        assert oci_storage.BUCKET_NAME == "poc-interactivetxt-media-src-bucket"
        assert oci_storage.HTML_BUCKET_NAME == "poc-interactivetxtbk1"
        assert oci_storage.VIDEO_BUCKET_NAME == "poc-interactivetxt-media-dst-bucket"

    def test_cfg_falls_back_when_config_missing(self, monkeypatch):
        monkeypatch.setattr(oci_storage, "_config", None)
        assert oci_storage._cfg("ANYTHING", "fallback") == "fallback"

    def test_cfg_treats_empty_string_as_unset(self, monkeypatch):
        class Cfg:
            SOME_KEY = ""

        monkeypatch.setattr(oci_storage, "_config", Cfg)
        assert oci_storage._cfg("SOME_KEY", "fallback") == "fallback"
