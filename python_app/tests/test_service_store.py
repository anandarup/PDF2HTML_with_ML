"""
Tests for the Phase 2 shared-state backends (ServiceJobStore + RedisProgressStore)
against an in-memory fake Redis — so the logic is verified without a live server.

The fake implements exactly the subset of the redis-py API these stores use:
get/set(ex)/sadd/smembers/hset/hget/hvals and a pipeline supporting
watch/multi/get/set/sadd/execute/unwatch.

Run:  python -m unittest tests.test_service_store -v
"""

from __future__ import annotations

import unittest

from state.job_store import ServiceJobStore
from progress_tracker import RedisProgressStore


class FakeRedis:
    def __init__(self):
        self.kv: dict = {}
        self.sets: dict = {}
        self.hashes: dict = {}

    # --- string ---
    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None):
        self.kv[k] = v
        return True

    # --- set ---
    def sadd(self, k, *vals):
        self.sets.setdefault(k, set()).update(vals)
        return len(vals)

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    # --- hash ---
    def hset(self, k, field, value):
        self.hashes.setdefault(k, {})[field] = value
        return 1

    def hget(self, k, field):
        return self.hashes.get(k, {}).get(field)

    def hvals(self, k):
        return list(self.hashes.get(k, {}).values())

    # --- pipeline (context manager) ---
    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    """Minimal WATCH/MULTI pipeline: immediate-mode until multi(), then queued."""
    def __init__(self, r: FakeRedis):
        self._r = r
        self._queued = []
        self._in_multi = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def watch(self, *keys):
        # No real optimistic locking needed for single-threaded tests.
        return True

    def unwatch(self):
        return True

    def get(self, k):
        # In immediate (pre-multi) mode, return the live value like redis-py does.
        return self._r.get(k)

    def multi(self):
        self._in_multi = True

    def set(self, k, v, ex=None):
        if self._in_multi:
            self._queued.append(("set", k, v))
        else:
            self._r.set(k, v, ex=ex)

    def sadd(self, k, *vals):
        if self._in_multi:
            self._queued.append(("sadd", k, vals))
        else:
            self._r.sadd(k, *vals)

    def execute(self):
        for op in self._queued:
            if op[0] == "set":
                self._r.set(op[1], op[2])
            elif op[0] == "sadd":
                self._r.sadd(op[1], *op[2])
        self._queued = []
        self._in_multi = False


class ServiceJobStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = ServiceJobStore(FakeRedis())

    def test_create_get_roundtrip(self):
        self.store.create_job("j1", {"status": "processing", "ref_id": "r-1", "progress": {}})
        rec = self.store.get_job("j1")
        self.assertEqual(rec["status"], "processing")
        self.assertIsNone(self.store.get_job("missing"))

    def test_find_by_ref_id_indexed(self):
        self.store.create_job("j1", {"status": "done", "ref_id": "abc-123"})
        found = self.store.find_by_ref_id("abc-123")
        self.assertEqual(found["ref_id"], "abc-123")
        self.assertIsNone(self.store.find_by_ref_id("nope"))

    def test_set_progress_merges_fields(self):
        self.store.create_job("j1", {"status": "processing", "progress": {}, "ref_id": None})
        self.store.set_progress("j1", "analyzing", "Analyzing…", {"page_count": 3})
        self.store.set_progress("j1", "extracting_images", "Extracting…", {"image_count": 5})
        rec = self.store.get_job("j1")
        self.assertEqual(rec["stage"], "extracting_images")
        self.assertEqual(rec["progress"]["page_count"], 3)   # earlier field preserved
        self.assertEqual(rec["progress"]["image_count"], 5)

    def test_set_progress_missing_job_is_noop(self):
        # Must not raise if the job disappeared (parity with FileJobStore).
        self.store.set_progress("ghost", "x", "y", {"k": 1})

    def test_terminal_update_is_idempotent(self):
        rec = {"status": "done", "ref_id": "r-9", "edit_url": "/output/a/b.html", "result": {}}
        self.store.update_job("j2", rec)
        self.store.update_job("j2", rec)  # re-apply
        self.assertEqual(self.store.get_job("j2")["status"], "done")
        self.assertEqual(self.store.find_by_ref_id("r-9")["ref_id"], "r-9")

    def test_stale_ref_index_guarded(self):
        # If a ref points at a job whose ref_id changed, lookup returns None.
        r = self.store._r
        self.store.create_job("j3", {"status": "done", "ref_id": "old"})
        # Simulate the job being overwritten with a different ref_id, index stale.
        self.store.update_job("j3", {"status": "done", "ref_id": "new"})
        r.set(self.store._ref_key("old"), "j3")  # force a stale index entry
        self.assertIsNone(self.store.find_by_ref_id("old"))
        self.assertEqual(self.store.find_by_ref_id("new")["ref_id"], "new")


class RedisProgressStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = RedisProgressStore(FakeRedis())

    def test_save_and_get(self):
        rec = self.store.save("learner1", "vid.mp4", 30.0, 100.0)
        self.assertAlmostEqual(rec["percentage"], 30.0)
        self.assertFalse(rec["completed"])
        got = self.store.get("learner1", "vid.mp4")
        self.assertEqual(got["current_time"], 30.0)

    def test_completion_at_90pct(self):
        rec = self.store.save("l", "v", 95.0, 100.0)
        self.assertTrue(rec["completed"])

    def test_get_all_and_upsert(self):
        self.store.save("l", "v1", 10, 100)
        self.store.save("l", "v2", 20, 100)
        self.store.save("l", "v1", 50, 100)  # update existing
        allrec = self.store.get_all("l")
        self.assertEqual(len(allrec), 2)  # v1 updated, not duplicated
        v1 = self.store.get("l", "v1")
        self.assertEqual(v1["current_time"], 50)

    def test_get_missing(self):
        self.assertIsNone(self.store.get("l", "none"))
        self.assertEqual(self.store.get_all("nobody"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
