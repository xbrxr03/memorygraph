from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

DEFAULT_BUSY_TIMEOUT_MS = 5_000
MIGRATION_NAME_PATTERN = re.compile(r"^(?P<version>\d+)_.*\.sql$")


@dataclass(frozen=True)
class DatabaseConfig:
    path: str | Path
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
    enable_wal: bool = True


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def connect(config: DatabaseConfig) -> sqlite3.Connection:
    database_path = Path(config.path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.isolation_level = None
    configure_connection(
        connection, busy_timeout_ms=config.busy_timeout_ms, enable_wal=config.enable_wal
    )
    return connection


def configure_connection(
    connection: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    enable_wal: bool = True,
) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA temp_store = MEMORY")
    if enable_wal:
        connection.execute("PRAGMA journal_mode = WAL")


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, mode: str = "IMMEDIATE"
) -> Iterator[sqlite3.Connection]:
    normalized_mode = mode.upper()
    if normalized_mode not in {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}:
        raise ValueError(f"Unsupported SQLite transaction mode: {mode!r}")

    if connection.in_transaction:
        savepoint_name = f"sp_{id(connection)}_{abs(hash(object()))}"
        connection.execute(f"SAVEPOINT {savepoint_name}")
        try:
            yield connection
        except Exception:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            raise
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        return

    connection.execute(f"BEGIN {normalized_mode}")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def backup_database(connection: sqlite3.Connection, destination: str | Path) -> Path:
    backup_path = Path(destination)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(backup_path)) as backup_connection:
        connection.backup(backup_connection)
    return backup_path


class MigrationRunner:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def migrations(self) -> list[Migration]:
        migration_package = resources.files("memorygraph.storage.migrations")
        migrations: list[Migration] = []
        for migration_file in sorted(migration_package.iterdir(), key=lambda item: item.name):
            match = MIGRATION_NAME_PATTERN.match(migration_file.name)
            if not match:
                continue
            migrations.append(
                Migration(
                    version=int(match.group("version")),
                    name=migration_file.name,
                    sql=migration_file.read_text(encoding="utf-8"),
                )
            )
        return migrations

    def current_version(self) -> int:
        if not self._table_exists("schema_metadata"):
            return 0
        row = self._connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return 0
        return int(row["value"])

    def migrate(self) -> int:
        migrations = self.migrations()
        current_version = self.current_version()
        pending = [migration for migration in migrations if migration.version > current_version]
        if not pending:
            return current_version

        script_parts = ["BEGIN IMMEDIATE;"]
        for migration in pending:
            script_parts.append(migration.sql)
            script_parts.append(
                f"""
                INSERT INTO schema_metadata(key, value)
                VALUES ('schema_version', '{migration.version}')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """
            )
        script_parts.append("COMMIT;")

        try:
            self._connection.executescript("\n".join(script_parts))
        except Exception:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise

        return self.current_version()

    def _table_exists(self, table_name: str) -> bool:
        row = self._connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None
