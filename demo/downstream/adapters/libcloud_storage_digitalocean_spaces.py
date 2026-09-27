"""Public-contract tests for the DigitalOcean Spaces downstream task.

This adapter intentionally exercises the provider registry and observable
connection behavior.  It does not import connection classes by names chosen
by a historical implementation.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


WORKSPACE = Path(os.environ["DOWNSTREAM_WORKSPACE"]).resolve()
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from libcloud.common.base import Connection  # noqa: E402
from libcloud.common.types import LibcloudError  # noqa: E402
from libcloud.storage.providers import get_driver  # noqa: E402
from libcloud.storage.types import Provider  # noqa: E402


class DigitalOceanSpacesPublicContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.driver_cls = get_driver(Provider.DIGITALOCEAN_SPACES)

    def _make_driver(self, **kwargs):
        # BaseDriver connects during construction.  The contract tests only
        # inspect local driver state, so no external DigitalOcean connection
        # should be made.
        with patch.object(Connection, "connect", return_value=None):
            return self.driver_cls("key", "secret", **kwargs)

    def test_provider_registration_and_s3_surface(self) -> None:
        driver = self._make_driver(region="nyc3")
        for method in (
            "iterate_containers",
            "get_container",
            "get_object",
            "create_container",
            "delete_container",
            "upload_object",
            "delete_object",
            "download_object",
            "list_container_objects",
        ):
            self.assertTrue(hasattr(driver, method), method)

    def test_regions_select_the_public_endpoint(self) -> None:
        # The historical change introduces nyc3. Other regions were added
        # later and are not part of this task's observed contract.
        driver = self._make_driver(region="nyc3")
        self.assertEqual(driver.connection.host, "nyc3.digitaloceanspaces.com")

    def test_signature_version_is_selected_by_behavior(self) -> None:
        for version in ("2", "4"):
            with self.subTest(signature_version=version):
                driver = self._make_driver(region="nyc3", signature_version=version)
                self.assertEqual(driver.signature_version, version)
                self.assertEqual(driver.connection.host, "nyc3.digitaloceanspaces.com")

    def test_invalid_public_arguments_are_rejected(self) -> None:
        with self.assertRaises(LibcloudError):
            self._make_driver(region="not-a-spaces-region")
        with self.assertRaises(ValueError):
            self._make_driver(signature_version="5")


if __name__ == "__main__":
    unittest.main()
