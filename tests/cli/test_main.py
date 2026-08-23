from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from typer.testing import CliRunner

from memorygraph import MemoryGraph
from memorygraph.cli.main import app
from memorygraph.storage.database import DatabaseConfig, connect


class CliMainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_init_creates_database_and_is_idempotent(self) -> None:
        database = self.tmp_path / "state" / "memorygraph.db"

        first = self.runner.invoke(app, ["init", "--database", str(database)])
        second = self.runner.invoke(app, ["init", "--database", str(database)])

        self.assertEqual(first.exit_code, 0)
        self.assertIn("Initialized MemoryGraph at", first.stdout)
        self.assertEqual(second.exit_code, 0)
        self.assertIn("MemoryGraph ready at", second.stdout)
        self.assertTrue(database.exists())

    def test_doctor_reports_missing_database_with_action(self) -> None:
        database = self.tmp_path / "missing" / "memorygraph.db"

        result = self.runner.invoke(app, ["doctor", "--database", str(database)])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("[WARN] database file: database does not exist yet", result.stdout)
        self.assertIn("run `memorygraph init --database", result.stdout)

    def test_doctor_succeeds_for_initialized_database(self) -> None:
        database = self.tmp_path / "healthy.db"
        self.runner.invoke(app, ["init", "--database", str(database)])

        result = self.runner.invoke(app, ["doctor", "--database", str(database)])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("[OK] sqlite features:", result.stdout)
        self.assertIn("[OK] schema version:", result.stdout)
        self.assertIn("[OK] memorygraph open:", result.stdout)

    def test_doctor_fails_for_non_sqlite_path(self) -> None:
        database = self.tmp_path / "not-a-db.sqlite3"
        database.write_text("definitely not sqlite", encoding="utf-8")

        result = self.runner.invoke(app, ["doctor", "--database", str(database)])

        self.assertEqual(result.exit_code, 1)
        self.assertTrue(
            "[FAIL] sqlite features:" in result.stdout
            or "[FAIL] memorygraph open:" in result.stdout
        )

    def test_doctor_warns_when_schema_is_not_initialized(self) -> None:
        database = self.tmp_path / "empty.sqlite3"
        connection = connect(DatabaseConfig(path=database))
        connection.close()

        result = self.runner.invoke(app, ["doctor", "--database", str(database)])

        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "[WARN] schema version: database exists but has not been initialized by MemoryGraph",
            result.stdout,
        )
        self.assertIn("run `memorygraph init --database", result.stdout)

    def test_packaged_migrations_are_present(self) -> None:
        result = self.runner.invoke(app, ["doctor", "--database", "tmp/memorygraph.db"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("[OK] migration resources:", result.stdout)

    def test_install_codex_writes_project_scoped_mcp_config_idempotently(self) -> None:
        project = self.tmp_path / "project"
        project.mkdir()

        first = self.runner.invoke(app, ["install-codex", "--project", str(project)])
        second = self.runner.invoke(app, ["install-codex", "--project", str(project)])

        self.assertEqual(first.exit_code, 0)
        self.assertEqual(second.exit_code, 0)
        config = (project / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(config.count("[mcp_servers.memorygraph]"), 1)
        self.assertIn(f"command = {json.dumps(sys.executable)}", config)
        self.assertIn('args = ["-m", "memorygraph.mcp", ".memorygraph/memory.db"]', config)
        self.assertIn('default_tools_approval_mode = "writes"', config)
        self.assertIn("required = false", config)

    def test_recall_exposes_a_strict_token_budget(self) -> None:
        database = self.tmp_path / "memory.db"
        with MemoryGraph.open(database) as memory:
            bank = memory.create_bank("project:bounded-cli")
            observation = memory.observe(
                "budget " * 20,
                bank=bank.id,
                source_key="bounded-observation",
            )
            memory.assert_claim(
                bank=bank.id,
                subject="CLI",
                predicate="budget_note",
                object="bounded output",
                object_kind="string",
                observation_id=observation.id,
            )

        result = self.runner.invoke(
            app,
            [
                "recall",
                "budget",
                "--bank",
                "project:bounded-cli",
                "--max-tokens",
                "3",
                "--database",
                str(database),
            ],
        )

        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertEqual(result.stdout, "")

    def test_install_codex_repairs_legacy_section_and_preserves_other_tables(self) -> None:
        project = self.tmp_path / "project"
        config_path = project / ".codex" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            '[mcp_servers.memorygraph]\ncommand = "missing-memorygraph-mcp"\n'
            'args = ["old.db"]\n\n[features]\nexample = true\n',
            encoding="utf-8",
        )

        result = self.runner.invoke(app, ["install-codex", "--project", str(project)])

        self.assertEqual(result.exit_code, 0, result.stdout)
        config = config_path.read_text(encoding="utf-8")
        self.assertEqual(config.count("[mcp_servers.memorygraph]"), 1)
        self.assertIn(f"command = {json.dumps(sys.executable)}", config)
        self.assertIn("[features]\nexample = true", config)

    def test_probe_codex_command_runs_configured_and_local_lifecycle(self) -> None:
        project = self.tmp_path / "project"
        project.mkdir()
        installed = self.runner.invoke(app, ["install-codex", "--project", str(project)])
        self.assertEqual(installed.exit_code, 0, installed.stdout)

        result = self.runner.invoke(app, ["probe-codex", "--project", str(project)])

        self.assertEqual(result.exit_code, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(
            {launch["source"] for launch in report["launches"]},
            {"config", "local_module"},
        )

    def test_project_obsidian_command_generates_reviewable_markdown(self) -> None:
        database = self.tmp_path / "memory.db"
        output = self.tmp_path / "vault"
        with MemoryGraph.open(database) as memory:
            memory.create_bank("project:acme")

        result = self.runner.invoke(
            app,
            [
                "project-obsidian",
                "--bank",
                "project:acme",
                "--output",
                str(output),
                "--database",
                str(database),
            ],
        )

        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertTrue((output / "MemoryGraph.md").exists())
        self.assertIn('"files_written": 3', result.stdout)

    def test_record_attempt_command_persists_procedural_memory(self) -> None:
        database = self.tmp_path / "memory.db"
        with MemoryGraph.open(database) as memory:
            memory.create_bank("project:attempts")

        result = self.runner.invoke(
            app,
            [
                "record-attempt",
                "Run migrations before the worker",
                "--bank",
                "project:attempts",
                "--source-key",
                "attempt:1",
                "--task",
                "start worker",
                "--outcome",
                "success",
                "--applicability-json",
                '{"database":"sqlite"}',
                "--database",
                str(database),
            ],
        )

        self.assertEqual(result.exit_code, 0, result.stdout)
        with MemoryGraph.open(database) as memory:
            attempts = memory.recall_attempts(
                bank="project:attempts", query_text="worker migrations"
            )
        self.assertEqual(len(attempts), 1)

    def test_dogfood_bootstrap_command_is_idempotent(self) -> None:
        database = self.tmp_path / "memory.db"
        first = self.runner.invoke(
            app,
            ["dogfood", "bootstrap", "--database", str(database)],
        )
        second = self.runner.invoke(
            app,
            ["dogfood", "bootstrap", "--database", str(database)],
        )

        self.assertEqual(first.exit_code, 0, first.stdout)
        self.assertEqual(second.exit_code, 0, second.stdout)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))


if __name__ == "__main__":
    unittest.main()
