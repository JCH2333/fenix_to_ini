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


def _drop_legacy_upsert_indexes(conn: sqlite3.Connection, table: str):
    """Remove unique indexes created by converter versions before v1.2."""
    indexes = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name=? AND name LIKE 'idx_upsert_%'",
        (table,),
    ).fetchall()
    for row in indexes:
        name = row[0].replace('"', '""')
        conn.execute(f'DROP INDEX "{name}"')


def batch_upsert(conn: sqlite3.Connection, table: str, columns: list[str],
                 rows: list[tuple], conflict_columns: list[str], batch_size: int = 5000):
    """
    Merge rows without adding constraints or deleting existing duplicates.

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

    _drop_legacy_upsert_indexes(conn, table)

    key_indexes = [columns.index(column) for column in conflict_columns]
    key_sql = ','.join(conflict_columns)
    existing = {}
    for record in conn.execute(f"SELECT rowid, {key_sql} FROM {table}"):
        key = tuple(record[index + 1] for index in range(len(conflict_columns)))
        existing.setdefault(key, []).append(record[0])

    updates = []
    inserts = []
    for row in rows:
        key = tuple(row[index] for index in key_indexes)
        candidates = existing.get(key)
        if candidates:
            updates.append(tuple(row) + (candidates.pop(0),))
        else:
            inserts.append(row)

    assignments = ','.join(f'{column}=?' for column in columns)
    placeholders = ','.join('?' for _ in columns)
    col_names = ','.join(columns)
    with conn:
        conn.executemany(
            f"UPDATE {table} SET {assignments} WHERE rowid=?",
            updates,
        )
        conn.executemany(
            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
            inserts,
        )

    print(f"  [{table}] merged {len(rows)} rows "
          f"(updated {len(updates)}, inserted {len(inserts)})")
    return len(rows)


def batch_merge_by_coordinates(
    conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    rows: list[tuple],
    identifier_column: str,
    latitude_column: str,
    longitude_column: str,
    tolerance: float = 0.001,
):
    """Merge navigation points by identifier and nearby coordinates."""
    if not rows:
        print(f"  [{table}] 0 rows to merge (empty)")
        return 0

    _drop_legacy_upsert_indexes(conn, table)
    ident_index = columns.index(identifier_column)
    lat_index = columns.index(latitude_column)
    lon_index = columns.index(longitude_column)

    existing = {}
    query = (
        f"SELECT rowid, {identifier_column}, {latitude_column}, {longitude_column} "
        f"FROM {table}"
    )
    for rowid, ident, lat, lon in conn.execute(query):
        if ident and lat is not None and lon is not None:
            existing.setdefault(str(ident).strip(), []).append((rowid, lat, lon))

    used_rowids = set()
    updates = []
    inserts = []
    max_distance_sq = tolerance * tolerance
    for row in rows:
        ident = str(row[ident_index] or '').strip()
        lat = row[lat_index]
        lon = row[lon_index]
        best = None
        if ident and lat is not None and lon is not None:
            for candidate in existing.get(ident, []):
                rowid, old_lat, old_lon = candidate
                if rowid in used_rowids:
                    continue
                distance_sq = (lat - old_lat) ** 2 + (lon - old_lon) ** 2
                if distance_sq <= max_distance_sq and (
                    best is None or distance_sq < best[0]
                ):
                    best = (distance_sq, rowid)
        if best is None:
            inserts.append(row)
        else:
            used_rowids.add(best[1])
            updates.append(tuple(row) + (best[1],))

    assignments = ','.join(f'{column}=?' for column in columns)
    placeholders = ','.join('?' for _ in columns)
    col_names = ','.join(columns)
    with conn:
        conn.executemany(
            f"UPDATE {table} SET {assignments} WHERE rowid=?",
            updates,
        )
        conn.executemany(
            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
            inserts,
        )

    print(f"  [{table}] merged {len(rows)} rows "
          f"(updated {len(updates)}, inserted {len(inserts)})")
    return len(rows)


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
