"""Public-contract tests for the Celery GCS backend task.

The adapter resolves the backend through Celery's public URL registry and
uses fake cloud clients.  It does not import a backend class by a name chosen
by the historical implementation or patch its private helper methods.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
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
        if self.value is None:
            raise FakeNotFound("missing")
        return self.value

    def upload_from_string(self, value, **kwargs):
        del kwargs
        self.value = value

    def exists(self):
        return self.value is not None and not self.deleted

    def delete(self, **kwargs):
        del kwargs
        self.deleted = True


class FakeBucket:
    lifecycle_rules = []

    def __init__(self) -> None:
        self.blobs = {}

    def blob(self, name):
        return self.blobs.setdefault(name, FakeBlob())


class FakeHttp:
    def mount(self, *args, **kwargs):
        del args, kwargs


class FakeClient:
    def __init__(self, project=None) -> None:
        self.project = project
        self._http = FakeHttp()
        self._auth_request = types.SimpleNamespace(session=FakeHttp())
        self._bucket = FakeBucket()

    def bucket(self, name):
        del name
        return self._bucket


class FakeFirestoreAdminClient:
    def get_field(self, request):
        del request
        return types.SimpleNamespace(
            ttl_config=types.SimpleNamespace(state="ACTIVE")
        )


class FakeField:
    class TtlConfig:
        class State:
            ACTIVE = "ACTIVE"
            CREATING = "CREATING"


class CeleryGCSContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend_cls, self.backend_url = by_url(
            "gs://bucket/base?gcs_project=project"
        )
        self.module = __import__(
            self.backend_cls.__module__, fromlist=["backend"]
        )
        self.app = Celery("gcs-contract")
        self.app.conf.update(
            gcs_bucket="bucket",
            gcs_project="project",
            gcs_base_path="base",
            gcs_ttl=0,
        )

    def _backend(self):
        fake_storage = types.SimpleNamespace(blob=types.SimpleNamespace(NotFound=FakeNotFound))
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
            patch.object(self.module, "Client", FakeClient, create=True),
            patch.object(self.module, "requests", fake_requests, create=True),
            patch.object(self.module, "DEFAULT_RETRY", None, create=True),
        ]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])
        return self.backend_cls(app=self.app, url=self.backend_url)

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

    def test_public_configuration_validation(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            self.app.conf.gcs_bucket = None
            self._backend()

        self.app.conf.gcs_bucket = "bucket"
        self.app.conf.gcs_ttl = -1
        with self.assertRaises(ImproperlyConfigured):
            self._backend()

    def test_client_is_cached(self) -> None:
        backend = self._backend()
        self.assertIs(backend.client, backend.client)


if __name__ == "__main__":
    unittest.main()
