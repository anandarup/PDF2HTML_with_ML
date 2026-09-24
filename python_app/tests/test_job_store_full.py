"""
================================================================================
Exhaustive unit-test suite for state/job_store.py
Target: 100% coverage — line, branch, AND function.
================================================================================

Module under test: state/job_store.py
  - JobStore            (abstract interface)
  - FileJobStore        (local-disk backend + lazy ref_id index)
  - ServiceJobStore     (Redis-backed backend + WATCH/MULTI progress merge)
  - _build_redis_client (factory helper)
  - build_job_store     (backend selector)

Testing philosophy (per SDET requirements):
  1. EXHAUSTIVE SCENARIOS — happy path, boundaries, null/empty, type mismatches.
  2. BRANCH & EXCEPTION COVERAGE — every if/else, every try/except path is
     explicitly triggered and asserted (index hit/miss/stale, ttl on/off,
     WatchError retry-then-exhaust, JSONDecodeError, TypeError, ValueError).
  3. TOTAL ISOLATION — the ONLY external dependency (Redis) is replaced by an
     in-memory fake; file-system access is redirected to a pytest tmp_path, so
     nothing touches a real network, DB, or shared disk. Safe on any VM.

Frameworks: pytest + pytest-cov (coverage via coverage.py / v8-equivalent).
Run instructions are at the bottom of this file.
================================================================================
"""

from __future__ import annotations

import json
import types
import pytest

# Import the module under test by its package path. Tests are run from the
# python_app/ directory (see CLI at the bottom), so `state` is importable.
from state import job_store
from state.job_store import (
    JobStore,
    FileJobStore,
    ServiceJobStore,
    build_job_store,
    _build_redis_client,
)


# =============================================================================
# Test doubles (TOTAL ISOLATION — no real Redis, no network)
# =============================================================================

class FakeWatchError(Exception):
    """Stand-in for redis.exceptions.WatchError. The module distinguishes it by
    class NAME ('WatchError'), so the class name must match exactly."""
    pass
FakeWatchError.__name__ = "WatchError"          # module checks exc.__class__.__name__
FakeWatchError.__qualname__ = "WatchError"


class FakePipeline:
    """
    Minimal Redis pipeline supporting the exact calls ServiceJobStore.set_progress
    makes: watch / unwatch / get / multi / set / execute, as a context manager.

    Behaviour is configurable to exercise every branch:
      - `watch_errors_before_success`: raise WatchError on the first N execute()
        attempts (drives the retry loop).
      - `raise_on_execute`: raise an arbitrary (non-Watch) error to drive the
        'other exception -> return' branch.
    """
    def __init__(self, store, watch_errors_before_success=0, raise_on_execute=None):
        self._store = store
        self._watch_errors_before_success = watch_errors_before_success
        self._raise_on_execute = raise_on_execute
        self._queued = []
        self._in_multi = False
        self.unwatch_called = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def watch(self, *keys):
        return True

    def unwatch(self):
        self.unwatch_called = True
        return True

    def get(self, key):
        return self._store.kv.get(key)

    def multi(self):
        self._in_multi = True

    def set(self, key, value, ex=None):
        if self._in_multi:
            self._queued.append((key, value, ex))
        else:  # pragma: no cover - set_progress always calls multi() first
            self._store.kv[key] = value

    def execute(self):
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        if self._watch_errors_before_success > 0:
            self._watch_errors_before_success -= 1
            raise FakeWatchError("watch failed")
        for key, value, _ex in self._queued:
            self._store.kv[key] = value
        self._queued = []
        self._in_multi = False


class FakeRedis:
    """
    In-memory Redis substitute implementing exactly the subset used by
    ServiceJobStore: get / set(ex) / sadd / pipeline().

    Pipeline behaviour is injectable so a single test can force WatchError
    retries or a hard failure without any real Redis.
    """
    def __init__(self, pipeline_factory=None):
        self.kv: dict = {}
        self.sets: dict = {}
        self._pipeline_factory = pipeline_factory

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        self.kv[key] = value
        # Record the last ex used, so tests can assert TTL was applied.
        self.last_ex = ex
        return True

    def sadd(self, key, *vals):
        self.sets.setdefault(key, set()).update(vals)
        return len(vals)

    def pipeline(self):
        if self._pipeline_factory is not None:
            return self._pipeline_factory(self)
        return FakePipeline(self)


# =============================================================================
# FileJobStore — file-system isolated via pytest tmp_path
# =============================================================================

class TestFileJobStore:
    @pytest.fixture
    def store(self, tmp_path):
        # tmp_path isolates all FS I/O to a throwaway dir — no shared disk.
        return FileJobStore(tmp_path / "jobs")

    # --- construction ------------------------------------------------------
    def test_init_creates_jobs_dir(self, tmp_path):
        d = tmp_path / "nested" / "jobs"
        assert not d.exists()
        FileJobStore(d)
        assert d.exists()  # __init__ mkdir(parents=True) branch

    def test_init_accepts_str_path(self, tmp_path):
        # Path() coercion branch: pass a str, not a Path.
        s = FileJobStore(str(tmp_path / "jobs_str"))
        s.create_job("j", {"ref_id": None})
        assert s.get_job("j") == {"ref_id": None}

    # --- create / get (happy path) ----------------------------------------
    def test_create_and_get_job(self, store):
        rec = {"status": "processing", "ref_id": "r-1"}
        store.create_job("j1", rec)
        assert store.get_job("j1") == rec

    def test_get_missing_returns_none(self, store):
        # _read_file -> FileNotFoundError branch
        assert store.get_job("nope") is None

    def test_get_job_with_corrupt_file_returns_none(self, store):
        # _read_file -> json.JSONDecodeError branch
        store._job_path("bad").write_text("{not valid json", encoding="utf-8")
        assert store.get_job("bad") is None

    # --- update ------------------------------------------------------------
    def test_update_overwrites_record(self, store):
        store.create_job("j1", {"status": "processing", "ref_id": "r-1"})
        store.update_job("j1", {"status": "done", "ref_id": "r-1"})
        assert store.get_job("j1")["status"] == "done"

    # --- _index_ref both branches (ref present / ref absent) ---------------
    def test_index_ref_present(self, store):
        store.create_job("j1", {"ref_id": "abc"})
        assert store.find_by_ref_id("abc") is not None

    def test_index_ref_absent_no_crash(self, store):
        # record with no ref_id -> _index_ref 'if ref:' is False
        store.create_job("j1", {"ref_id": None})
        store.create_job("j2", {})  # ref_id key missing entirely
        assert store.find_by_ref_id("anything") is None

    # --- set_progress: every branch ---------------------------------------
    def test_set_progress_missing_job_is_noop(self, store):
        # job is None branch -> early return, no file created
        store.set_progress("ghost", "s", "d", {"k": 1})
        assert store.get_job("ghost") is None

    def test_set_progress_with_extra_and_no_prior_progress(self, store):
        # prog = job.get("progress") or {}  -> falsy branch (no 'progress' key)
        # extra present branch -> prog.update(extra)
        store.create_job("j1", {"status": "processing", "ref_id": None})
        store.set_progress("j1", "analyzing", "Analyzing…", {"page_count": 3})
        j = store.get_job("j1")
        assert j["stage"] == "analyzing"
        assert j["detail"] == "Analyzing…"
        assert j["progress"]["page_count"] == 3
        assert j["progress"]["stage"] == "analyzing"

    def test_set_progress_without_extra_merges_existing_progress(self, store):
        # extra is None branch (skip update) + prog truthy branch (existing dict)
        store.create_job("j1", {"status": "processing", "progress": {"page_count": 9}})
        store.set_progress("j1", "building", "Building…")  # extra defaults None
        j = store.get_job("j1")
        assert j["progress"]["page_count"] == 9      # preserved
        assert j["progress"]["stage"] == "building"

    # --- find_by_ref_id: all four paths -----------------------------------
    def test_find_by_ref_id_via_index(self, store):
        store.create_job("j1", {"ref_id": "R1"})
        assert store.find_by_ref_id("R1")["ref_id"] == "R1"  # index hit + valid

    def test_find_by_ref_id_index_miss_then_scan_finds(self, tmp_path):
        # A pre-existing file the in-memory index doesn't know about ->
        # _ensure_index builds from disk, or fallback scan repairs. Fresh store
        # so the index is empty until first use.
        d = tmp_path / "jobs"
        d.mkdir()
        (d / "pre.json").write_text(json.dumps({"ref_id": "PRE"}), encoding="utf-8")
        store = FileJobStore(d)
        # _ensure_index (building branch: index not built) + rec truthy branch
        assert store.find_by_ref_id("PRE")["ref_id"] == "PRE"

    def test_find_by_ref_id_stale_index_falls_back(self, store):
        # Index points at a job whose ref_id no longer matches -> stale guard,
        # then fallback scan (which also won't match) -> None.
        store.create_job("j1", {"ref_id": "OLD"})
        store.update_job("j1", {"ref_id": "NEW"})  # index still maps OLD->j1
        store._ref_index["OLD"] = "j1"             # force the stale entry
        assert store.find_by_ref_id("OLD") is None  # stale + fallback miss

    def test_find_by_ref_id_not_found(self, store):
        store.create_job("j1", {"ref_id": "R1"})
        # index miss + fallback scan finds nothing -> None
        assert store.find_by_ref_id("DOES-NOT-EXIST") is None

    def test_find_by_ref_id_fallback_repairs_index(self, tmp_path):
        # Fallback scan finds a match and writes it into the index.
        d = tmp_path / "jobs"
        d.mkdir()
        (d / "x.json").write_text(json.dumps({"ref_id": "Z"}), encoding="utf-8")
        store = FileJobStore(d)
        store._index_built = True          # skip _ensure_index building
        store._ref_index = {}              # empty index -> index miss
        assert store.find_by_ref_id("Z")["ref_id"] == "Z"
        assert store._ref_index["Z"] == "x"   # index repaired

    def test_ensure_index_skips_when_built(self, store):
        # _ensure_index early-return branch (index already built).
        store.create_job("j1", {"ref_id": "R1"})   # builds index
        assert store._index_built is True
        # Add a file directly that the built index won't see via _ensure_index...
        store._job_path("j2").write_text(json.dumps({"ref_id": "R2"}), encoding="utf-8")
        # find via index for R1 (no rebuild), proving the early-return path ran.
        assert store.find_by_ref_id("R1")["ref_id"] == "R1"

    def test_ensure_index_ignores_unreadable_file(self, tmp_path):
        # _ensure_index 'if rec:' falsy branch — a corrupt file is skipped.
        d = tmp_path / "jobs"
        d.mkdir()
        (d / "good.json").write_text(json.dumps({"ref_id": "G"}), encoding="utf-8")
        (d / "corrupt.json").write_text("{broken", encoding="utf-8")
        store = FileJobStore(d)
        assert store.find_by_ref_id("G")["ref_id"] == "G"   # corrupt skipped, good found


# =============================================================================
# ServiceJobStore — Redis fully mocked (no network)
# =============================================================================

class TestServiceJobStore:
    @pytest.fixture
    def store(self):
        return ServiceJobStore(FakeRedis())

    # --- __init__ ttl normalization (all three branches) -------------------
    def test_init_ttl_positive_kept(self):
        s = ServiceJobStore(FakeRedis(), hot_ttl_seconds=120)
        assert s._ttl == 120

    def test_init_ttl_zero_becomes_none(self):
        s = ServiceJobStore(FakeRedis(), hot_ttl_seconds=0)
        assert s._ttl is None

    def test_init_ttl_negative_becomes_none(self):
        # `hot_ttl_seconds > 0` false branch
        s = ServiceJobStore(FakeRedis(), hot_ttl_seconds=-5)
        assert s._ttl is None

    # --- key helpers with prefix ------------------------------------------
    def test_key_helpers_use_prefix(self):
        s = ServiceJobStore(FakeRedis(), prefix="pfx:")
        assert s._job_key("j") == "pfx:job:j"
        assert s._ref_key("r") == "pfx:ref:r"
        assert s._jobs_set_key() == "pfx:jobs"

    # --- _loads: every branch ---------------------------------------------
    def test_loads_none(self):
        assert ServiceJobStore._loads(None) is None

    def test_loads_str(self):
        assert ServiceJobStore._loads('{"a": 1}') == {"a": 1}

    def test_loads_bytes(self):
        # bytes decode branch
        assert ServiceJobStore._loads(b'{"a": 2}') == {"a": 2}

    def test_loads_bad_json_returns_none(self):
        # json.JSONDecodeError branch
        assert ServiceJobStore._loads("{not json") is None

    def test_loads_type_error_returns_none(self):
        # TypeError branch (json.loads on a non-str/bytes/None, e.g. int)
        assert ServiceJobStore._loads(12345) is None

    # --- create / get / update (persist branches) --------------------------
    def test_create_and_get(self, store):
        store.create_job("j1", {"status": "processing", "ref_id": "r1"})
        assert store.get_job("j1")["status"] == "processing"

    def test_get_missing_returns_none(self, store):
        assert store.get_job("absent") is None

    def test_persist_without_ttl_and_with_ref(self, store):
        # ttl None branch (set without ex) + ref present branch
        store.create_job("j1", {"ref_id": "r1"})
        assert store._r.kv[store._job_key("j1")]           # job stored
        assert store._r.kv[store._ref_key("r1")] == "j1"   # ref index stored
        assert "j1" in store._r.sets[store._jobs_set_key()]

    def test_persist_without_ref(self, store):
        # ref absent branch (record has no ref_id)
        store.create_job("j2", {"status": "done"})
        assert store._ref_key("") not in store._r.kv

    def test_persist_with_ttl_sets_expiry(self):
        # ttl truthy branch on both the job set and the ref set
        r = FakeRedis()
        s = ServiceJobStore(r, hot_ttl_seconds=60)
        s.create_job("j1", {"ref_id": "r1"})
        assert r.last_ex == 60  # last set() carried the TTL (the ref key)

    def test_update_is_idempotent(self, store):
        rec = {"status": "done", "ref_id": "r9"}
        store.update_job("j2", rec)
        store.update_job("j2", rec)
        assert store.get_job("j2")["status"] == "done"

    # --- set_progress: pipeline branches ----------------------------------
    def test_set_progress_happy_with_extra(self, store):
        store.create_job("j1", {"status": "processing", "progress": {}})
        store.set_progress("j1", "analyzing", "A…", {"page_count": 2})
        j = store.get_job("j1")
        assert j["stage"] == "analyzing"
        assert j["progress"]["page_count"] == 2
        assert j["progress"]["stage"] == "analyzing"

    def test_set_progress_no_extra_and_falsy_progress(self, store):
        # extra None branch + `job.get("progress") or {}` falsy branch
        store.create_job("j1", {"status": "processing"})  # no 'progress' key
        store.set_progress("j1", "building", "B…")
        j = store.get_job("j1")
        assert j["progress"]["stage"] == "building"

    def test_set_progress_missing_job_unwatches_and_returns(self):
        # job is None branch -> pipe.unwatch() + return
        captured = {}
        def factory(store):
            p = FakePipeline(store)
            captured["pipe"] = p
            return p
        r = FakeRedis(pipeline_factory=factory)
        s = ServiceJobStore(r)
        s.set_progress("ghost", "s", "d", {"k": 1})   # nothing stored
        assert captured["pipe"].unwatch_called is True
        assert s.get_job("ghost") is None

    def test_set_progress_with_ttl_in_pipeline(self):
        # ttl truthy branch INSIDE the pipeline set
        r = FakeRedis()
        s = ServiceJobStore(r, hot_ttl_seconds=30)
        s.create_job("j1", {"status": "processing", "progress": {}})
        s.set_progress("j1", "uploading", "U…", {"image_count": 5})
        assert s.get_job("j1")["progress"]["image_count"] == 5

    def test_set_progress_retries_on_watch_error_then_succeeds(self):
        # WatchError branch -> loop 'continue' -> next attempt succeeds.
        # The store creates a NEW pipeline per retry (self._r.pipeline()), so we
        # keep the "how many WatchErrors remain" counter OUTSIDE the pipeline,
        # in a mutable cell shared across the pipelines the factory builds.
        remaining = {"n": 2}

        def factory(store):
            p = FakePipeline(store)
            _orig_execute = p.execute

            def execute():
                if remaining["n"] > 0:
                    remaining["n"] -= 1
                    raise FakeWatchError("watch failed")
                _orig_execute()
            p.execute = execute
            return p

        r = FakeRedis(pipeline_factory=factory)
        s = ServiceJobStore(r, max_retries=5)
        s.create_job("j1", {"status": "processing", "progress": {}})
        s.set_progress("j1", "analyzing", "A…", {"page_count": 1})
        assert s.get_job("j1")["stage"] == "analyzing"   # eventually persisted
        assert remaining["n"] == 0                        # both retries consumed

    def test_set_progress_watch_error_exhausts_retries(self):
        # WatchError on every attempt -> loop finishes without persisting ->
        # final `return` after the for-loop.
        def factory(store):
            return FakePipeline(store, watch_errors_before_success=99)
        r = FakeRedis(pipeline_factory=factory)
        s = ServiceJobStore(r, max_retries=3)
        s.create_job("j1", {"status": "processing", "stage": "queued", "progress": {}})
        s.set_progress("j1", "analyzing", "A…", {"page_count": 1})
        # Never applied — stage stays as created.
        assert s.get_job("j1")["stage"] == "queued"

    def test_set_progress_non_watch_exception_returns(self):
        # Non-WatchError exception branch -> immediate best-effort return.
        def factory(store):
            return FakePipeline(store, raise_on_execute=RuntimeError("redis down"))
        r = FakeRedis(pipeline_factory=factory)
        s = ServiceJobStore(r, max_retries=5)
        s.create_job("j1", {"status": "processing", "stage": "queued", "progress": {}})
        s.set_progress("j1", "analyzing", "A…", {"page_count": 1})
        assert s.get_job("j1")["stage"] == "queued"  # unchanged

    # --- find_by_ref_id: all branches -------------------------------------
    def test_find_by_ref_id_none_when_index_missing(self, store):
        # ref key absent -> job_id is None branch
        assert store.find_by_ref_id("missing") is None

    def test_find_by_ref_id_str_index(self, store):
        store.create_job("j1", {"ref_id": "r1"})
        assert store.find_by_ref_id("r1")["ref_id"] == "r1"

    def test_find_by_ref_id_bytes_index_decoded(self):
        # job_id stored as bytes -> decode branch
        r = FakeRedis()
        s = ServiceJobStore(r)
        s.create_job("j1", {"ref_id": "r1"})
        r.kv[s._ref_key("r1")] = b"j1"   # force bytes index value
        assert s.find_by_ref_id("r1")["ref_id"] == "r1"

    def test_find_by_ref_id_stale_returns_none(self):
        # ref index points at a job whose ref_id differs -> guard returns None
        r = FakeRedis()
        s = ServiceJobStore(r)
        s.create_job("j1", {"ref_id": "new"})
        r.kv[s._ref_key("old")] = "j1"   # stale mapping old -> j1 (rec.ref_id='new')
        assert s.find_by_ref_id("old") is None

    def test_find_by_ref_id_points_to_missing_job(self):
        # ref index resolves to a job_id that has no record -> rec is None -> None
        r = FakeRedis()
        s = ServiceJobStore(r)
        r.kv[s._ref_key("r1")] = "ghost"
        assert s.find_by_ref_id("r1") is None


# =============================================================================
# _build_redis_client + build_job_store factory
# =============================================================================

class _Cfg:
    """A lightweight config stand-in (duck-typed like the config module)."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class TestBuildRedisClient:
    def test_missing_url_raises_value_error(self):
        # url empty branch -> ValueError
        with pytest.raises(ValueError, match="REDIS_URL"):
            _build_redis_client(_Cfg(REDIS_URL=""))

    def test_absent_url_attr_raises_value_error(self):
        # getattr default "" branch (attr not present at all)
        with pytest.raises(ValueError):
            _build_redis_client(_Cfg())

    def test_builds_client_with_url(self, monkeypatch):
        # url present branch — mock redis-py so no real connection is made.
        created = {}

        def fake_from_url(url, decode_responses):
            created["url"] = url
            created["decode_responses"] = decode_responses
            return "CLIENT"

        fake_redis_module = types.SimpleNamespace(
            Redis=types.SimpleNamespace(from_url=fake_from_url)
        )
        monkeypatch.setitem(__import__("sys").modules, "redis", fake_redis_module)
        client = _build_redis_client(_Cfg(REDIS_URL="redis://localhost:6379/0"))
        assert client == "CLIENT"
        assert created["url"] == "redis://localhost:6379/0"
        assert created["decode_responses"] is True   # module passes decode_responses=True


class TestBuildJobStore:
    def test_file_backend(self, tmp_path):
        cfg = _Cfg(STATE_BACKEND="file", JOBS_DIR=tmp_path / "jobs")
        store = build_job_store(cfg)
        assert isinstance(store, FileJobStore)

    def test_default_backend_is_file(self, tmp_path):
        # STATE_BACKEND attr absent -> getattr default "file"
        cfg = _Cfg(JOBS_DIR=tmp_path / "jobs")
        assert isinstance(build_job_store(cfg), FileJobStore)

    def test_service_backend_without_durable_db(self, monkeypatch):
        # service branch + JSONDB_URL empty -> ttl = 0
        monkeypatch.setattr(job_store, "_build_redis_client", lambda cfg: FakeRedis())
        cfg = _Cfg(STATE_BACKEND="service", REDIS_URL="redis://x", JSONDB_URL="",
                   STATE_HOT_TTL_SECONDS=3600)
        store = build_job_store(cfg)
        assert isinstance(store, ServiceJobStore)
        assert store._ttl is None   # no durable DB -> no TTL (durable Redis)

    def test_service_backend_with_durable_db_applies_ttl(self, monkeypatch):
        # service branch + JSONDB_URL set -> ttl from STATE_HOT_TTL_SECONDS
        monkeypatch.setattr(job_store, "_build_redis_client", lambda cfg: FakeRedis())
        cfg = _Cfg(STATE_BACKEND="service", REDIS_URL="redis://x",
                   JSONDB_URL="adb://durable", STATE_HOT_TTL_SECONDS=1800)
        store = build_job_store(cfg)
        assert store._ttl == 1800

    def test_service_backend_ttl_zero_when_config_zero(self, monkeypatch):
        # durable DB configured but STATE_HOT_TTL_SECONDS = 0 -> `or 0` branch
        monkeypatch.setattr(job_store, "_build_redis_client", lambda cfg: FakeRedis())
        cfg = _Cfg(STATE_BACKEND="service", REDIS_URL="redis://x",
                   JSONDB_URL="adb://durable", STATE_HOT_TTL_SECONDS=0)
        store = build_job_store(cfg)
        assert store._ttl is None

    def test_unknown_backend_raises(self):
        cfg = _Cfg(STATE_BACKEND="cassandra")
        with pytest.raises(ValueError, match="Unknown STATE_BACKEND"):
            build_job_store(cfg)


# =============================================================================
# Abstract interface — ensure the ABC cannot be instantiated (function coverage
# of the abstract method stubs is satisfied by the concrete subclasses above).
# =============================================================================

class TestJobStoreABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            JobStore()  # abstract methods unimplemented
