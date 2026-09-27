"""Public-contract tests for the Celery GCS result backend task.

The adapter resolves the backend through Celery's URL registry and exercises
its public operations against a small fake Google Cloud Storage SDK surface.
"""

from __future__ import annotations

import os
import sys
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


WORKSPACE = Path(os.environ["DOWNSTREAM_WORKSPACE"]).resolve()
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from celery import Celery  # noqa: E402
from celery.app.backends import by_url  # noqa: E402
from celery.exceptions import ImproperlyConfigured  # noqa: E402


class FakeNotFound(Exception):
    pass


class FakeBlob:
    def __init__(self) -> None:
        self.value = None
        self.custom_time = None
        self.deleted = False

    def download_as_bytes(self, **kwargs):
        del kwargs
        if self.value is None or self.deleted:
            raise FakeNotFound("missing")
        return self.value

    def upload_from_string(self, value, **kwargs):
        del kwargs
        self.value = value
        self.deleted = False

    def exists(self):
        return self.value is not None and not self.deleted

    def delete(self, **kwargs):
        del kwargs
        self.deleted = True


class FakeBucket:
    def __init__(self, lifecycle_rules=None) -> None:
        self.blobs = {}
        self.lifecycle_rules = list(lifecycle_rules or [])

    def blob(self, name):
        return self.blobs.setdefault(name, FakeBlob())

    def reload(self):
        return None


class FakeHttp:
    def mount(self, *args, **kwargs):
        del args, kwargs


class FakeClient:
    instances = []
    bucket_lifecycle_rules = []

    def __init__(self, project=None) -> None:
        time.sleep(0.01)  # expose duplicate construction under concurrent first use
        self.project = project
        session = FakeHttp()
        self._http = types.SimpleNamespace(
            _auth_request=types.SimpleNamespace(session=session)
        )
        self._bucket = FakeBucket(self.bucket_lifecycle_rules)
        self.instances.append(self)

    def bucket(self, name):
        del name
        return self._bucket


class FakeFirestoreAdminClient:
    def get_field(self, request):
        del request
        return types.SimpleNamespace(ttl_config=types.SimpleNamespace(state="ACTIVE"))


class FakeField:
    class TtlConfig:
        class State:
            ACTIVE = "ACTIVE"
            CREATING = "CREATING"


class CeleryGCSContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend_cls, self.backend_url = by_url("gs://bucket/base?gcs_project=project")
        self.module = __import__(self.backend_cls.__module__, fromlist=["backend"])
        self.app = Celery("gcs-contract")
        self.app.conf.update(
            gcs_bucket="bucket",
            gcs_project="project",
            gcs_base_path="base",
            gcs_ttl=0,
        )
        FakeClient.instances.clear()
        FakeClient.bucket_lifecycle_rules = []

    def _patch_sdk(self):
        fake_storage = types.SimpleNamespace(
            Client=FakeClient,
            blob=types.SimpleNamespace(NotFound=FakeNotFound),
        )
        fake_firestore = types.SimpleNamespace(Increment=lambda value: value)
        fake_admin = types.SimpleNamespace(
            FirestoreAdminClient=FakeFirestoreAdminClient,
            GetFieldRequest=lambda name: types.SimpleNamespace(name=name),
            Field=FakeField,
        )
        fake_requests = types.SimpleNamespace(
            adapters=types.SimpleNamespace(HTTPAdapter=lambda **kwargs: Mock(**kwargs))
        )
        patches = [
            patch.object(self.module, "storage", fake_storage, create=True),
            patch.object(self.module, "firestore", fake_firestore, create=True),
            patch.object(self.module, "firestore_admin_v1", fake_admin, create=True),
            # Some SDK versions expose Client directly; matching both entry
            # points keeps the fake compatible without coupling tests to one.
            patch.object(self.module, "Client", FakeClient, create=True),
            patch.object(self.module, "requests", fake_requests, create=True),
            patch.object(self.module, "DEFAULT_RETRY", None, create=True),
        ]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])

    def _backend(self, url=None):
        self._patch_sdk()
        return self.backend_cls(app=self.app, url=url or self.backend_url)

    def test_backend_is_registered_by_public_url(self) -> None:
        self.assertTrue(self.backend_cls)
        self.assertEqual(self.backend_url, "gs://bucket/base?gcs_project=project")

    def test_public_key_value_operations_support_bytes_and_mget(self) -> None:
        backend = self._backend()
        backend.set(b"key", b"value")
        self.assertEqual(backend.get(b"key"), b"value")
        self.assertEqual(backend.mget([b"key", b"missing"]), [b"value", None])
        backend.delete(b"key")
        self.assertIsNone(backend.get(b"key"))

    def test_configuration_validation_rejects_missing_bucket_and_negative_ttl(self) -> None:
        self.app.conf.gcs_bucket = None
        with self.assertRaises(ImproperlyConfigured):
            self._backend(url="gs:///")

        self.app.conf.gcs_bucket = "bucket"
        self.app.conf.gcs_ttl = -1
        with self.assertRaises(ImproperlyConfigured):
            self._backend()

    def test_client_is_reused_for_operations_in_the_same_process(self) -> None:
        backend = self._backend()
        backend.set(b"one", b"1")
        backend.set(b"two", b"2")
        self.assertEqual(backend.get(b"one"), b"1")
        self.assertEqual(len(FakeClient.instances), 1)

    def test_client_cache_is_safe_on_concurrent_first_use(self) -> None:
        backend = self._backend()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda index: backend.set(str(index).encode(), b"v"), range(8)))
        self.assertEqual(len(FakeClient.instances), 1)

    def test_cached_client_is_recreated_after_fork(self) -> None:
        backend = self._backend()
        pid = [100]
        if hasattr(self.module, "getpid"):
            pid_patch = patch.object(self.module, "getpid", side_effect=lambda: pid[0])
        elif hasattr(self.module, "os"):
            pid_patch = patch.object(self.module.os, "getpid", side_effect=lambda: pid[0])
        else:
            self.skipTest("backend has no observable process ID lookup")
        with pid_patch:
            backend.set(b"before-fork", b"v1")
            pid[0] = 200
            backend.set(b"after-fork", b"v2")
        self.assertGreaterEqual(len(FakeClient.instances), 2)

    def test_positive_ttl_requires_a_bucket_lifecycle_rule(self) -> None:
        self.app.conf.gcs_ttl = 1
        with self.assertRaises(ImproperlyConfigured):
            self._backend()

    def test_positive_ttl_accepts_bucket_delete_lifecycle_policy(self) -> None:
        self.app.conf.gcs_ttl = 1
        FakeClient.bucket_lifecycle_rules = [
            {"action": {"type": "Delete"}, "condition": {"age": 1}}
        ]
        backend = self._backend()
        backend.set(b"key", b"value")
        self.assertEqual(backend.get(b"key"), b"value")


if __name__ == "__main__":
    unittest.main()
