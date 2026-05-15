"""Per-session DuckDB session management for File Analyzer.

This module provides a thin wrapper around DuckDB that:

1. Creates a unique per-session working directory (to avoid collisions and
   file overwrites when multiple users run the app simultaneously).
2. Loads CSV/pipe-delimited input files into a per-session DuckDB database.
3. Ensures that all queries that need stable ordering default to sorting by
   the dataset's primary-key (FileKey) columns.

Later UI layers (Visualize and Data Grid) rely on these invariants:
- Deterministic ordering (by FileKey)
- Session isolation for temp files
- A clear entrypoint for building filtered queries
"""

from __future__ import annotations

import atexit
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from file_analyzer.meta_parser import MetaDefinition


@dataclass(frozen=True)
class DuckDBSessionConfig:
    """Configuration for :class:`DuckDBSession`.

    Purpose
    -------
    Centralize environment-driven knobs for session setup.

    Internal Logic
    ---------------
    The config object is consumed by :class:`DuckDBSession` to determine:
    - where the temp directory lives,
    - whether we use in-memory or file-backed database storage,
    - DuckDB thread configuration.

    Parameters
    ----------
    temp_base_dir:
        Base directory for per-session temp dirs.
    duckdb_storage_mode:
        Either ``"memory"`` for in-memory databases or ``"file"`` for per-session
        database files stored in the temp dir.
    duckdb_threads:
        Desired DuckDB thread count (best-effort; DuckDB may ignore invalid values).
    """

    temp_base_dir: Path
    duckdb_storage_mode: str = "file"
    duckdb_threads: Optional[int] = None
    cleanup_on_close: bool = True


class DuckDBSession:
    """A session-scoped DuckDB connection wrapper.

    Purpose
    -------
    Provide a safe, isolated execution context for loading data and running
    filtered/sorted queries driven by the dataset's metadata.

    Internal Logic
    ---------------
    - On initialization, a unique temp directory is created.
    - A DuckDB connection is opened against either an in-memory database or a
      per-session database file inside the temp directory.
    - The session provides helpers to build stable ``ORDER BY`` clauses based
      on the dataset's FileKey column list.

    Notes
    -----
    This wrapper intentionally does not implement full filter logic yet; that
    depends on the UI filter state and will be built in subsequent todos.

    Parameters
    ----------
    meta:
        Parsed metadata definition for the dataset.
    config:
        Session configuration.
    """

    def __init__(self, meta: MetaDefinition, config: DuckDBSessionConfig) -> None:
        self._meta = meta
        self._config = config
        self._temp_dir = self._create_temp_dir()
        self._duckdb_conn = None
        self._database_path: Optional[Path] = None

        # Ensure we clean up on process exit even if a caller forgets to close.
        atexit.register(self._safe_cleanup)

    @property
    def meta(self) -> MetaDefinition:
        """Return the session metadata definition."""

        return self._meta

    @property
    def temp_dir(self) -> Path:
        """Return the per-session temp directory path."""

        return self._temp_dir

    def _create_temp_dir(self) -> Path:
        """Create a unique per-session temp directory.

        Purpose
        -------
        Keep all session-generated files isolated.

        Returns
        -------
        Path
            Path to a newly created unique temp directory.
        """

        self._config.temp_base_dir.mkdir(parents=True, exist_ok=True)
        return Path(
            tempfile.mkdtemp(
                prefix="file_analyzer_",
                dir=str(self._config.temp_base_dir),
            )
        )

    def connect(self) -> None:
        """Open the DuckDB connection for this session.

        Raises
        ------
        RuntimeError
            If DuckDB is not installed or if the session is already connected.
        """

        if self._duckdb_conn is not None:
            return

        try:
            import duckdb  # type: ignore
        except ModuleNotFoundError as e:
            raise RuntimeError(
                "DuckDB is required for DuckDBSession but is not installed. "
                "Install the UI dependencies and data dependencies."
            ) from e

        database_path: Optional[Path] = None
        if self._config.duckdb_storage_mode == "file":
            database_path = self._temp_dir / "session.duckdb"
            # Ensure parent directory exists; temp dir is already created.
            database_path_str = str(database_path)
            self._database_path = database_path
        elif self._config.duckdb_storage_mode == "memory":
            database_path_str = ":memory:"
            self._database_path = None
        else:
            raise ValueError(
                f"Invalid duckdb_storage_mode={self._config.duckdb_storage_mode!r}. "
                "Use 'memory' or 'file'."
            )

        self._duckdb_conn = duckdb.connect(database=database_path_str)

        if self._config.duckdb_threads is not None:
            try:
                self._duckdb_conn.execute(
                    f"PRAGMA threads={int(self._config.duckdb_threads)};"
                )
            except Exception:
                # Best-effort only: invalid thread counts should not crash the UI.
                pass

    @property
    def connection(self):
        """Return the active DuckDB connection.

        Purpose
        -------
        Provide a clear access point for later query-building functions.

        Returns
        -------
        duckdb.DuckDBPyConnection
            Active DuckDB connection.

        Raises
        ------
        RuntimeError
            If :meth:`connect` was not called.
        """

        if self._duckdb_conn is None:
            raise RuntimeError("DuckDBSession is not connected yet. Call connect() first.")
        return self._duckdb_conn

    @property
    def database_path(self) -> Optional[Path]:
        """Return the per-session DuckDB database path if file-backed.

        Purpose
        -------
        Enable concurrent worker computations by allowing each worker to open
        its own DuckDB connection to the same per-session database file.

        Returns
        -------
        Optional[Path]
            Path to the DuckDB database file when storage mode is ``"file"``.
            Returns ``None`` for in-memory sessions.
        """

        return self._database_path

    def order_by_file_keys_sql(self, table_alias: Optional[str] = None) -> str:
        """Build an ``ORDER BY`` clause using FileKey columns.

        Purpose
        -------
        Ensure deterministic row ordering across:
        - initial dataset loads,
        - pagination,
        - chart↔grid synchronization.

        Internal Logic
        ---------------
        Create ``ORDER BY`` terms for each file key column using the provided
        table alias (if any).

        Parameters
        ----------
        table_alias:
            Optional alias used in SQL queries, e.g. ``t`` producing ``t.col``.

        Returns
        -------
        str
            SQL fragment starting with ``ORDER BY``.
        """

        if not self._meta.file_key_columns:
            return ""

        if table_alias:
            terms = [f"{table_alias}.{col}" for col in self._meta.file_key_columns]
        else:
            terms = [f"{col}" for col in self._meta.file_key_columns]

        return "ORDER BY " + ", ".join(terms)

    def load_csv_as_table(
        self,
        data_path: str | Path,
        delimiter: str,
        table_name: str = "data",
        header: bool = True,
    ) -> None:
        """Load a CSV/pipe-delimited file into DuckDB as a table.

        Purpose
        -------
        Provide a consistent dataset-loading entrypoint for later stats and filtering.

        Internal Logic
        ---------------
        Uses DuckDB's CSV reader to load the file, then:
        - keeps the dataset accessible as ``table_name``,
        - does not persist ordering; ordering is applied at query-time.

        Parameters
        ----------
        data_path:
            Path to the input dataset file.
        delimiter:
            Field delimiter used in the data file (e.g. ``'|'`` or `','`).
        table_name:
            DuckDB table name used for subsequent queries.
        header:
            Whether the input file contains a header row.
        """

        self.connect()
        path = Path(data_path)
        if not path.exists():
            raise FileNotFoundError(f"Data file does not exist: {path}")

        # Import locally so unit tests can run without duckdb installed.
        import duckdb  # type: ignore

        delim = delimiter
        if delim == "\\t":
            delim = "\t"

        # Read into a DuckDB table. Use CREATE OR REPLACE to support reruns
        # within the same session object.
        delim_sql = delim.replace("'", "''")
        header_int = 1 if header else 0
        self.connection.execute(
            f"""
            CREATE OR REPLACE TABLE {table_name} AS
            SELECT *
            FROM read_csv_auto(
                '{str(path).replace("'", "''")}',
                delim='{delim_sql}',
                header={header_int}
            )
            """
        )

        # Apply deterministic ordering by FileKey in downstream queries,
        # not here (DuckDB tables have no intrinsic ordering).
        _ = duckdb  # quiet unused linters if needed

    def safe_filtered_query_sql(
        self,
        base_table: str,
        where_sql: str,
        select_columns: str = "*",
        table_alias: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> str:
        """Build a filtered SQL query with FileKey sorting and optional pagination.

        Purpose
        -------
        Provide a stable query skeleton that the UI can use for:
        - grid pagination,
        - chart-table filtered subsets,
        - export.

        Internal Logic
        ---------------
        - Start from ``base_table``.
        - Apply ``where_sql`` if non-empty.
        - Apply ``ORDER BY`` on FileKey columns.
        - Optionally apply LIMIT/OFFSET.

        Parameters
        ----------
        base_table:
            Name of the DuckDB table.
        where_sql:
            SQL expression for the WHERE clause, without the ``WHERE`` keyword.
        select_columns:
            SQL columns selection.
        table_alias:
            Optional alias to qualify ORDER BY columns.
        limit:
            Optional LIMIT.
        offset:
            Optional OFFSET.

        Returns
        -------
        str
            Complete SQL query string.
        """

        alias = f"{base_table} {table_alias}" if table_alias else base_table

        where_clause = f"WHERE {where_sql}" if where_sql.strip() else ""
        order_clause = self.order_by_file_keys_sql(table_alias=table_alias)
        limit_clause = ""
        if limit is not None:
            limit_clause = f" LIMIT {int(limit)}"
        if offset is not None:
            limit_clause += f" OFFSET {int(offset)}"

        return (
            f"SELECT {select_columns} FROM {alias} "
            f"{where_clause} {order_clause} {limit_clause};"
        )

    def close(self) -> None:
        """Close the DuckDB connection and cleanup temp resources.

        Purpose
        -------
        Allow explicit lifecycle management and early cleanup.
        """

        if self._duckdb_conn is not None:
            try:
                self._duckdb_conn.close()
            except Exception:
                pass
            self._duckdb_conn = None

        if self._config.cleanup_on_close:
            self._safe_cleanup()

    def _safe_cleanup(self) -> None:
        """Best-effort cleanup of the temp directory."""

        try:
            if self._temp_dir.exists():
                # Use os.walk based delete to support Windows paths safely.
                for root, dirs, files in os.walk(self._temp_dir, topdown=False):
                    for name in files:
                        try:
                            Path(root, name).unlink(missing_ok=True)  # type: ignore[arg-type]
                        except Exception:
                            pass
                    for name in dirs:
                        try:
                            Path(root, name).rmdir()
                        except Exception:
                            pass
                try:
                    self._temp_dir.rmdir()
                except Exception:
                    pass
        except Exception:
            # Cleanup should never crash the app.
            pass

