from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memorygraph import BankNotFoundError, MemoryGraph


class EvidenceLedgerIntegrationTests(unittest.TestCase):
    def test_create_bank_and_idempotently_observe_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.db"
            with MemoryGraph.open(database) as memory:
                bank = memory.create_bank("project:demo", name="Demo")
                repeated_bank = memory.create_bank("project:demo", name="Ignored new name")

                first = memory.observe(
                    "We use Hatchling.",
                    bank="project:demo",
                    source_key="thread:1:turn:1",
                )
                repeated = memory.observe(
                    "We use Hatchling.",
                    bank="project:demo",
                    source_key="thread:1:turn:1",
                )

            self.assertEqual(repeated_bank.id, bank.id)
            self.assertEqual(repeated.id, first.id)
            self.assertEqual(first.chunks[0].content, "We use Hatchling.")

    def test_bank_scope_is_required(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            MemoryGraph.open(Path(directory) / "memory.db") as memory,
            self.assertRaises(BankNotFoundError),
        ):
            memory.observe(
                "Unscoped content",
                bank="project:missing",
                source_key="thread:1",
            )


if __name__ == "__main__":
    unittest.main()
