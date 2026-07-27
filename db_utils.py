"""
SQLite database utilities for Fenix → iniBuilds conversion.

Handles:
- Read-only source connection (Fenix nd.db3)
- Read-write target connection (iniBuilds db.s3db)
- Batch INSERT with transactions
- Schema cloning from reference database
- Row counting and deduplication queries
"""

import sqlite3
import shutil
import os
from pathlib import Path


def open_source(path: str) -> sqlite3.Connection:
    """Open Fenix nd.db3 in read-only immutable mode."""
    uri = f"file:{path}?immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def open_target(path: str) -> sqlite3.Connection:
    """Open iniBuilds db.s3db for read-write."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrent performance
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")  # 64MB cache
    return conn


def copy_target_template(src: str, dst: str) -> str:
    """
    Copy the existing db.s3db as a working copy.

    Returns path to the working copy.
    """
    print(f"Copying {src} → {dst} ...")
    shutil.copy2(src, dst)
    # Also remove any WAL/SHM files from the copy
    for suffix in ('-wal', '-shm'):
        wal_path = dst + suffix
        if os.path.exists(wal_path):
            os.remove(wal_path)
    return dst


def batch_insert(conn: sqlite3.Connection, table: str, columns: list[str],
                 rows: list[tuple], batch_size: int = 5000):
    """
    Batch INSERT rows into a table with transaction management.

    Args:
        conn: Target database connection
        table: Target table name
        columns: Column names in order
        rows: List of row tuples matching columns order
        batch_size: Rows per transaction
    """
    if not rows:
        print(f"  [{table}] 0 rows to insert (empty)")
        return 0

    placeholders = ','.join(['?' for _ in columns])
    col_names = ','.join(columns)
    sql = f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})"

    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        with conn:
            conn.executemany(sql, batch)
        total += len(batch)

    print(f"  [{table}] inserted {total} rows")
    return total


def ensure_unique_index(conn: sqlite3.Connection, table: str, columns: list[str]):
    """
    Ensure a unique index exists on the given columns for a table.

    SQLite's `ON CONFLICT` clause requires a UNIQUE constraint or index on
    the conflict columns. The iniBuilds db.s3db schema does not declare
    PRIMARY KEY/UNIQUE constraints on most tables, so we create a
    dedicated unique index (idempotent, safe to call every run) before
    attempting an UPSERT. This index only affects query planning /
    constraint enforcement — it does not change how iniBuilds reads the
    table's columns.

    Some existing db.s3db data (e.g. from earlier stock/Navigraph
    imports) may already contain duplicate rows on the intended conflict
    columns. If index creation fails because of that, we deduplicate
    (keep the first occurrence, drop the rest by rowid) and retry once.
    """
    index_name = f"idx_upsert_{table}_{'_'.join(columns)}"
    col_list = ','.join(columns)
    try:
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({col_list})"
        )
    except sqlite3.IntegrityError:
        print(f"  [{table}] 发现基于 ({col_list}) 的重复历史记录，正在自动去重...")
        conn.execute(f"""
            DELETE FROM {table}
            WHERE rowid NOT IN (
                SELECT MIN(rowid) FROM {table} GROUP BY {col_list}
            )
        """)
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({col_list})"
        )


def batch_upsert(conn: sqlite3.Connection, table: str, columns: list[str],
                 rows: list[tuple], conflict_columns: list[str], batch_size: int = 5000):
    """
    Batch UPSERT (INSERT OR UPDATE) rows into a table with transaction management.

    Automatically ensures a unique index exists on conflict_columns, since
    SQLite requires one for the ON CONFLICT clause to work and the
    iniBuilds schema does not declare such constraints natively.

    Args:
        conn: Target database connection
        table: Target table name
        columns: Column names in order
        rows: List of row tuples matching columns order
        conflict_columns: Columns that define uniqueness (for ON CONFLICT clause)
        batch_size: Rows per transaction

    Returns:
        Number of rows processed
    """
    if not rows:
        print(f"  [{table}] 0 rows to upsert (empty)")
        return 0

    ensure_unique_index(conn, table, conflict_columns)

    placeholders = ','.join(['?' for _ in columns])
    col_names = ','.join(columns)
    conflict_keys = ','.join(conflict_columns)

    # Build UPDATE SET clause (all columns except conflict keys)
    update_columns = [col for col in columns if col not in conflict_columns]
    update_set = ','.join([f"{col}=excluded.{col}" for col in update_columns])

    sql = f"""
        INSERT INTO {table} ({col_names}) VALUES ({placeholders})
        ON CONFLICT({conflict_keys}) DO UPDATE SET {update_set}
    """

    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        with conn:
            conn.executemany(sql, batch)
        total += len(batch)

    print(f"  [{table}] upserted {total} rows")
    return total


def get_existing_ids(conn: sqlite3.Connection, table: str,
                     id_column: str, id_values: list) -> set:
    """
    Get set of IDs that already exist in the target table.

    Args:
        conn: Target database connection
        table: Target table name
        id_column: Column to check for existence
        id_values: List of values to check

    Returns:
        Set of values that exist
    """
    if not id_values:
        return set()

    placeholders = ','.join(['?' for _ in id_values])
    sql = f"SELECT DISTINCT {id_column} FROM {table} WHERE {id_column} IN ({placeholders})"
    cursor = conn.execute(sql, id_values)
    return {row[0] for row in cursor.fetchall()}


def get_existing_composite(conn: sqlite3.Connection, table: str,
                           columns: list[str], values: list[tuple]) -> set:
    """
    Check which composite-key rows already exist.

    Args:
        conn: Target database connection
        table: Target table name
        columns: Column names forming the composite key
        values: List of value tuples to check

    Returns:
        Set of value tuples that exist
    """
    if not values:
        return set()

    # Build WHERE clause for composite key
    conditions = ' AND '.join(f"{c} = ?" for c in columns)
    existing = set()
    for vals in values:
        sql = f"SELECT 1 FROM {table} WHERE {conditions} LIMIT 1"
        cursor = conn.execute(sql, vals)
        if cursor.fetchone():
            existing.add(vals)
    return existing


def create_table_from_template(conn: sqlite3.Connection, ref_db_path: str,
                               table_name: str):
    """
    Create an empty table in target using schema from reference database.

    Args:
        conn: Target database connection
        ref_db_path: Path to reference iniBuilds db.s3db
        table_name: Table name to create
    """
    ref_conn = sqlite3.connect(f"file:{ref_db_path}?immutable=1", uri=True)
    try:
        create_sql = ref_conn.execute(
            f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'"
        ).fetchone()
        if create_sql:
            conn.execute(create_sql[0])
            print(f"  [{table_name}] created empty table from template")
        else:
            print(f"  [{table_name}] WARNING: template table not found")
    finally:
        ref_conn.close()


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Count rows in a table."""
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def get_table_columns(conn: sqlite3.Connection, table: str) -> list[tuple]:
    """Get column info for a table. Returns list of (cid, name, type, notnull, dflt_value, pk)."""
    return conn.execute(f"PRAGMA table_info('{table}')").fetchall()


def vacuum(conn: sqlite3.Connection):
    """Optimize database after large inserts."""
    print("Running VACUUM...")
    conn.execute("VACUUM")
    print("VACUUM complete.")


def check_integrity(conn: sqlite3.Connection) -> bool:
    """Run integrity check on database."""
    result = conn.execute("PRAGMA integrity_check").fetchone()
    ok = result[0] == 'ok'
    if ok:
        print("Integrity check: OK")
    else:
        print(f"Integrity check FAILED: {result[0]}")
    return ok
