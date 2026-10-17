import tempfile
import unittest
import json
from pathlib import Path

from new_work.stores import Store, load_stores, save_stores
from new_work.transfer import TransferError, append_transfer_log, transfer_file


class StoreAndTransferTests(unittest.TestCase):
    def test_store_configuration_round_trips_twenty_stores(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "stores.json"
            stores = [
                Store.create(
                    f"synthetic-{index}",
                    f"/share/{index}",
                    scan_id=f"scan-{index}",
                    send_org_id=f"org-{index}",
                )
                for index in range(20)
            ]
            save_stores(stores, path)
            loaded = load_stores(path)
            self.assertEqual(len(loaded), 20)
            self.assertEqual(loaded[0].save_path, "/share/0")
            self.assertEqual(loaded[0].scan_id, "scan-0")
            self.assertEqual(loaded[0].send_org_id, "org-0")
            self.assertEqual(loaded[-1].store_id, stores[-1].store_id)

    def test_transfer_verifies_hash_and_requires_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            target = root / "share" / "result.txt"
            source.write_bytes(b"synthetic-work-result")

            result = transfer_file(source, target, job_id="synthetic-job")
            self.assertEqual(result.size, len(b"synthetic-work-result"))
            self.assertEqual(result.sha256, "16a3f49be4c48a59bc26fcf9ccc21b25e28aa8efb4451944f93f8a24244c091e")
            with self.assertRaises(FileExistsError):
                transfer_file(source, target, job_id="synthetic-job")

            source.write_bytes(b"synthetic-replacement")
            replaced = transfer_file(source, target, overwrite=True, job_id="synthetic-job")
            self.assertEqual(replaced.path.read_bytes(), b"synthetic-replacement")

    def test_legacy_store_with_different_carrier_roots_is_not_misrouted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stores.json"
            path.write_text(
                json.dumps([{
                    "name": "legacy",
                    "sk_destination": "/share/sk",
                    "kt_destination": "/share/kt",
                    "kt_database_template": "/db/template.db",
                }]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "서로 다른 SK/KT 목적지"):
                load_stores(path)

    def test_transfer_reports_destination_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            blocked_parent = root / "not-a-directory"
            source.write_text("synthetic", encoding="utf-8")
            blocked_parent.write_text("file", encoding="utf-8")

            with self.assertRaises(TransferError):
                transfer_file(source, blocked_parent / "result.txt")

    def test_transfer_log_contains_no_absolute_paths_or_ocr_values(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "logs" / "transfer.jsonl"
            append_transfer_log(
                log,
                job_id="synthetic-job",
                filename="/private/person/ORG.jpg",
                status="complete",
                size=42,
                sha256="synthetic-hash",
            )
            content = log.read_text(encoding="utf-8")
            self.assertIn('"filename":"ORG.jpg"', content)
            self.assertIn('"job_id":"synthetic-job"', content)
            self.assertNotIn("/private/person", content)
            self.assertNotIn("synthetic-name", content)


if __name__ == "__main__":
    unittest.main()
