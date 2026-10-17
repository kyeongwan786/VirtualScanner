import unittest
from pathlib import Path

from new_work.database.reference_audit import compare_reference_databases


class ReferenceDatabaseAuditTests(unittest.TestCase):
    def test_skt_insert_audit_lists_exactly_59_columns(self):
        audit_path = Path(__file__).resolve().parents[1] / "DATABASE_FIELD_AUDIT.md"
        text = audit_path.read_text(encoding="utf-8")
        section = text.split("| # | T_SCAN_PAPER 컬럼", 1)[1].split("## 결론", 1)[0]
        rows = [
            line for line in section.splitlines()
            if line.startswith("| ") and line.split("|", 2)[1].strip().isdigit()
        ]
        self.assertEqual(len(rows), 59)

    def test_supplied_reference_databases_have_matching_schema_and_expected_counts(self):
        references = Path(__file__).resolve().parents[2] / "idscanner"
        source = references / "uscan(20261017-075504).skt"
        result = references / "myCustomer.db"
        if not source.is_file() or not result.is_file():
            self.skipTest("reference databases are not present in this checkout")

        source_audit, result_audit, schemas_match = compare_reference_databases(source, result)

        self.assertTrue(schemas_match)
        self.assertEqual(source_audit.table_count, 18)
        self.assertEqual(result_audit.table_count, 18)
        self.assertEqual(source_audit.column_counts["T_SCAN_PAPER"], 80)
        self.assertEqual(source_audit.column_counts["T_SCAN_TEMP"], 82)
        self.assertEqual(source_audit.table_rows["T_SCAN_PAPER"], 400)
        self.assertEqual(result_audit.table_rows["T_SCAN_PAPER"], 399)
        self.assertEqual(source_audit.table_rows["T_SCAN_TEMP"], 50)
        self.assertEqual(result_audit.table_rows["T_SCAN_TEMP"], 50)
        self.assertTrue(all(value == 0 for value in source_audit.orphan_counts.values()))
        self.assertTrue(all(value == 0 for value in result_audit.orphan_counts.values()))


if __name__ == "__main__":
    unittest.main()
