from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from light_image_editor.secure_image_api_key import SecureImageApiKeyStore


@unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows")
class SecureImageApiKeyStoreTests(unittest.TestCase):
    def test_round_trip_encrypts_and_deletes_default_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SecureImageApiKeyStore(root)
            secret = "sk-test-secret-that-must-not-appear-in-file"

            self.assertIsNone(store.read())
            store.save(secret)

            payload = (root / "default-image-api.credential").read_bytes()
            self.assertNotIn(secret.encode("utf-8"), payload)
            self.assertEqual(store.read(), secret)
            self.assertTrue(store.delete())
            self.assertIsNone(store.read())


if __name__ == "__main__":
    unittest.main()
