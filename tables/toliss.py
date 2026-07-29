"""Compatibility checks and cleanup for the ToLiss DFDv2 loader."""

import sqlite3


def is_toliss_target(conn: sqlite3.Connection) -> bool:
    """Return whether the target exposes the ToLiss-specific IAP ctl field."""
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(tbl_pf_iaps)")
    }
    return "ctl" in columns


def count_runway_order_violations(conn: sqlite3.Connection) -> int:
    """Count order breaks in the runway scan used by the ToLiss loader."""
    return conn.execute(
        """
        SELECT COUNT(1)
        FROM (
            SELECT
                airport_identifier,
                LAG(airport_identifier) OVER (ORDER BY rowid) AS previous_identifier
            FROM tbl_pg_runways
            WHERE runway_length > 4757
        )
        WHERE airport_identifier < previous_identifier
        """
    ).fetchone()[0]


def sanitize_toliss_data(conn: sqlite3.Connection) -> dict[str, int]:
    """Enforce value constraints used by the AS346 fixed-memory loader."""
    stats: dict[str, int] = {}

    cursor = conn.execute(
        """
        UPDATE tbl_db_enroute_ndbnavaids
        SET magnetic_variation = 0.0
        WHERE magnetic_variation IS NULL
        """
    )
    stats["ndb_magvar_defaulted"] = cursor.rowcount

    cursor = conn.execute(
        """
        DELETE FROM tbl_ea_enroute_waypoints
        WHERE waypoint_identifier IS NULL
           OR length(waypoint_identifier) > 5
        """
    )
    stats["waypoints_removed"] = cursor.rowcount

    cursor = conn.execute(
        """
        DELETE FROM tbl_er_enroute_airways
        WHERE route_identifier IS NULL
           OR length(route_identifier) > 5
           OR waypoint_identifier IS NULL
           OR length(waypoint_identifier) > 5
           OR inbound_distance > 1000.0
           OR inbound_distance < 0.0
        """
    )
    stats["airways_removed"] = cursor.rowcount

    conn.execute(
        """
        UPDATE tbl_er_enroute_airways
        SET waypoint_description_code = 'E   '
        WHERE waypoint_description_code IS NULL
           OR length(waypoint_description_code) <> 4
        """
    )
    conn.execute(
        "UPDATE tbl_er_enroute_airways SET inbound_course = 0.0 "
        "WHERE inbound_course IS NULL"
    )
    conn.execute(
        "UPDATE tbl_er_enroute_airways SET inbound_distance = 0.0 "
        "WHERE inbound_distance IS NULL"
    )
    conn.execute(
        "UPDATE tbl_er_enroute_airways SET outbound_course = 0.0 "
        "WHERE outbound_course IS NULL"
    )

    runway_order_violations = count_runway_order_violations(conn)
    stats["runways_reordered"] = 0
    if runway_order_violations:
        runway_count = conn.execute(
            "SELECT COUNT(1) FROM tbl_pg_runways"
        ).fetchone()[0]
        conn.execute("DROP TABLE IF EXISTS temp._toliss_runways_ordered")
        conn.execute(
            """
            CREATE TEMP TABLE _toliss_runways_ordered AS
            SELECT *
            FROM tbl_pg_runways
            ORDER BY airport_identifier, runway_identifier, rowid
            """
        )
        conn.execute("DELETE FROM tbl_pg_runways")
        conn.execute(
            "INSERT INTO tbl_pg_runways SELECT * FROM _toliss_runways_ordered"
        )
        conn.execute("DROP TABLE temp._toliss_runways_ordered")
        stats["runways_reordered"] = runway_count

    conn.commit()
    return stats
