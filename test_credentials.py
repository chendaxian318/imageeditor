from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from light_image_editor.services.credentials import (
    CredentialAlreadyExistsError,
    CredentialStore,
    MemoryCredentialStore,
    WindowsDpapiCredentialStore,
)


class MemoryCredentialStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryCredentialStore()

    def test_implements_protocol_and_supports_save_read_delete(self) -> None:
        self.assertIsInstance(self.store, CredentialStore)
        self.assertIsNone(self.store.read("provider.test", "api-key"))

        self.store.save("provider.test", "api-key", "secret-value")

        self.assertEqual(self.store.read("provider.test", "api-key"), "secret-value")
        self.assertTrue(self.store.delete("provider.test", "api-key"))
        self.assertFalse(self.store.delete("provider.test", "api-key"))
        self.assertIsNone(self.store.read("provider.test", "api-key"))

    def test_overwrite_must_be_explicit_and_replaces_the_value(self) -> None:
        self.store.save("provider.test", "api-key", "first-secret")

        with self.assertRaises(CredentialAlreadyExistsError) as caught:
            self.store.save("provider.test", "api-key", "second-secret")
        self.assertNotIn("first-secret", str(caught.exception))
        self.assertNotIn("second-secret", str(caught.exception))
        self.assertEqual(self.store.read("provider.test", "api-key"), "first-secret")

        self.store.save("provider.test", "api-key", "second-secret", overwrite=True)
        self.assertEqual(self.store.read("provider.test", "api-key"), "second-secret")

    def test_target_and_name_are_strictly_validated_for_every_operation(self) -> None:
        invalid_targets = (
            "",
            " provider",
            "provider/name",
            "provider:name",
            ".provider",
            "a" * 129,
        )
        invalid_names = ("", " api-key", "api/key", "api:key", ".api-key", "a" * 65)

        for target in invalid_targets:
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    self.store.save(target, "api-key", "secret")
                with self.assertRaises(ValueError):
                    self.store.read(target, "api-key")
                with self.assertRaises(ValueError):
                    self.store.delete(target, "api-key")

        for name in invalid_names:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.store.save("provider.test", name, "secret")
                with self.assertRaises(ValueError):
                    self.store.read("provider.test", name)
                with self.assertRaises(ValueError):
                    self.store.delete("provider.test", name)

    def test_secret_and_overwrite_types_are_validated_without_secret_echo(self) -> None:
        with self.assertRaises(ValueError) as empty:
            self.store.save("provider.test", "api-key", "")
        self.assertNotIn("api-key", str(empty.exception))

        with self.assertRaises(TypeError):
            self.store.save("provider.test", "api-key", "secret", overwrite=1)  # type: ignore[arg-type]


@unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows")
class WindowsDpapiCredentialStoreTests(unittest.TestCase):
    def test_round_trip_overwrite_delete_and_ciphertext_confidentiality(self) -> None:
        target = "provider.remote"
        name = "api-key"
        first_secret = "secret-甲-123"
        second_secret = "secret-乙-456"
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            store = WindowsDpapiCredentialStore(directory)
            self.assertIsInstance(store, CredentialStore)

            store.save(target, name, first_secret)
            credential_files = list(directory.glob("*.credential"))
            self.assertEqual(len(credential_files), 1)
            payload = credential_files[0].read_bytes()
            self.assertNotIn(first_secret.encode("utf-8"), payload)
            self.assertNotIn(target.encode("ascii"), payload)
            self.assertNotIn(name.encode("ascii"), payload)
            self.assertEqual(store.read(target, name), first_secret)

            with self.assertRaises(CredentialAlreadyExistsError) as caught:
                store.save(target, name, second_secret)
            self.assertNotIn(first_secret, str(caught.exception))
            self.assertNotIn(second_secret, str(caught.exception))

            store.save(target, name, second_secret, overwrite=True)
            self.assertEqual(store.read(target, name), second_secret)
            self.assertTrue(store.delete(target, name))
            self.assertFalse(store.delete(target, name))
            self.assertIsNone(store.read(target, name))


if __name__ == "__main__":
    unittest.main()
