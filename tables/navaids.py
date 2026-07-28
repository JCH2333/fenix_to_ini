"""
Phase 3: Convert Fenix Navaids → iniBuilds VHF and NDB navaid tables.

Filters to Chinese airspace navaids.
Splits by type: VHF types → tbl_d_vhfnavaids, NDB types → tbl_db_enroute_ndbnavaids.
"""

import sys
import os
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import sqlite3
from freq import decode_freq, is_valid_frequency  # type: ignore[import-untyped]
from mappings import VHF_NAVAID_TYPES, NDB_NAVAID_TYPES, get_navaid_class  # type: ignore[import-untyped]
from region_lookup import RegionLookup  # type: ignore[import-untyped]


# Chinese airspace bounding box (approximate)
CN_LAT_MIN, CN_LAT_MAX = 15.0, 55.0
CN_LON_MIN, CN_LON_MAX = 70.0, 140.0

# Columns for VHF navaids table
TBL_D_COLUMNS = [
    'airport_identifier',       # VARCHAR(4)
    'area_code',                # VARCHAR(3) NOT NULL
    'continent',                # VARCHAR(40)
    'country',                  # VARCHAR(40)
    'datum_code',               # VARCHAR(3)
    'dme_elevation',            # INT
    'dme_ident',                # VARCHAR(4)
    'dme_latitude',             # FLOAT
    'dme_longitude',            # FLOAT
    'icao_code',                # VARCHAR(2)
    'ilsdme_bias',              # REAL
    'magnetic_variation',       # REAL
    'navaid_class',             # VARCHAR(5) NOT NULL
    'navaid_frequency',         # REAL NOT NULL
    'navaid_identifier',        # VARCHAR(4) NOT NULL
    'navaid_latitude',          # FLOAT
    'navaid_longitude',         # FLOAT
    'navaid_name',              # VARCHAR(30) NOT NULL
    'range',                    # SMALLINT
    'station_declination',      # REAL
]

# Columns for NDB navaids table
TBL_DB_COLUMNS = [
    'area_code',                # VARCHAR(3) NOT NULL
    'continent',                # VARCHAR(40)
    'country',                  # VARCHAR(40)
    'datum_code',               # VARCHAR(3)
    'icao_code',                # VARCHAR(2)
    'magnetic_variation',       # REAL
    'navaid_class',             # VARCHAR(5) NOT NULL
    'navaid_frequency',         # REAL NOT NULL
    'navaid_identifier',        # VARCHAR(4) NOT NULL
    'navaid_latitude',          # FLOAT
    'navaid_longitude',         # FLOAT
    'navaid_name',              # VARCHAR(30) NOT NULL
    'range',                    # SMALLINT
]

# Area code mapping by longitude for China
# East of ~100E → 'EEU' (Eastern Europe/Asia), which is what iniBuilds uses for China
# This is consistent with the existing data


def is_cn_airspace(lat: float, lon: float) -> bool:
    """Check if coordinates are within Chinese airspace."""
    if lat is None or lon is None:
        return False
    return (CN_LAT_MIN <= lat <= CN_LAT_MAX and
            CN_LON_MIN <= lon <= CN_LON_MAX)


def derive_area_icao(lat: float, lon: float, ident: str = '',
                     region_lookup: 'RegionLookup | None' = None,
                     nearest_apt: str | None = None) -> tuple[str, str]:
    """
    Derive area_code and icao_code for a navaid/waypoint.

    Priority:
    1. Cross-reference against 2607 NAIP CSV FIR data (most accurate)
    2. Fall back to nearest Chinese airport's ICAO prefix
    3. Fall back to a coarse longitude-based bucket (least accurate, last resort)
    """
    area_code = 'EEU'

    # 1. Cross-reference against 2607 CSV FIR data
    if region_lookup is not None:
        icao_code = region_lookup.get_navaid_icao(ident)
        if icao_code:
            return area_code, icao_code

    # 2. Fall back to nearest airport's ICAO prefix
    if nearest_apt and len(nearest_apt) >= 2:
        return area_code, nearest_apt[:2]

    # 3. Last-resort coarse longitude bucket (known to be inaccurate near
    #    FIR boundaries; only used when no better data is available)
    if lon < 97:
        icao_code = 'ZW'  # Xinjiang
    elif lon < 106:
        icao_code = 'ZL'  # Lanzhou
    elif lon < 114:
        icao_code = 'ZB'  # Beijing
    elif lon < 120:
        icao_code = 'ZS'  # Shanghai
    else:
        icao_code = 'ZY'  # Shenyang / far east

    return area_code, icao_code


def convert_navaids(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection,
                    airport_lookup: dict[int, str],
                    region_lookup: 'RegionLookup | None' = None):
    """
    Convert Chinese airspace navaids to iniBuilds format.

    Uses UPSERT: existing navaids are refreshed with the latest Fenix
    data, new navaids are inserted. Navaids whose decoded frequency
    falls outside the valid band for their type (e.g. foreign military
    TACAN stations using a different frequency scheme) are skipped with
    a warning instead of being written with bogus data.

    Args:
        airport_lookup: Dict mapping Fenix AirportID → ICAO code
        region_lookup: Optional RegionLookup for accurate FIR-based icao_code
    """
    print("\n=== Phase 3: Navaids ===")

    if region_lookup is None:
        region_lookup = RegionLookup()

    # Get all Fenix navaids
    fenix_navaids = src_conn.execute("""
        SELECT ID, Ident, Type, Name, Freq, Channel, Usage,
               Latitude, Longtitude, Elevation, SlavedVar,
               MagneticVariation, Range
        FROM Navaids
        ORDER BY ID
    """).fetchall()

    # Build reverse lookup: ICAO → AirportID set
    icao_to_airport_ids = {}
    for aid, icao in airport_lookup.items():
        if icao not in icao_to_airport_ids:
            icao_to_airport_ids[icao] = set()
        icao_to_airport_ids[icao].add(aid)

    # Get Chinese airport coordinates for proximity matching
    cn_airport_coords = {}
    for row in src_conn.execute("""
        SELECT ID, ICAO, Latitude, Longtitude FROM Airports
    """):
        aid = row['ID']
        if aid in airport_lookup:
            cn_airport_coords[row['ICAO']] = (row['Latitude'], row['Longtitude'])

    # Filter navaids in Chinese airspace
    vhf_rows = []
    ndb_rows = []
    vhf_new = 0
    vhf_updated = 0
    ndb_new = 0
    ndb_updated = 0
    freq_rejected = 0

    # Get existing navaid identifiers (for new vs. updated reporting)
    existing_vhf = set()
    for row in dst_conn.execute("SELECT navaid_identifier FROM tbl_d_vhfnavaids"):
        existing_vhf.add(row['navaid_identifier'])

    existing_ndb = set()
    for row in dst_conn.execute("SELECT navaid_identifier FROM tbl_db_enroute_ndbnavaids"):
        existing_ndb.add(row['navaid_identifier'])

    for n in fenix_navaids:
        lat = n['Latitude']
        lon = n['Longtitude']
        ntype = str(n['Type'])

        if not is_cn_airspace(lat, lon):
            continue

        # Skip ILS-DME (type 8) - handled by ILS table
        if ntype == '8':
            continue

        ident = (n['Ident'] or '').strip()
        if not ident:
            continue

        freq = decode_freq(n['Freq'], ntype)

        # Reject navaids whose frequency doesn't fall in a plausible band
        # for their type (e.g. foreign military TACAN using a different
        # encoding scheme) rather than writing bogus data silently.
        if not is_valid_frequency(freq, ntype):
            freq_rejected += 1
            continue

        # Find nearest Chinese airport for context
        nearest_apt = None
        for apt_icao, (apt_lat, apt_lon) in cn_airport_coords.items():
            # Quick check: within ~5 degrees
            if abs(lat - apt_lat) < 5 and abs(lon - apt_lon) < 5:
                nearest_apt = apt_icao
                break

        area_code, icao_code = derive_area_icao(lat, lon, ident, region_lookup, nearest_apt)
        navaid_class = get_navaid_class(ntype, n['Usage'], n['Range'], n['Elevation'])

        if ntype in VHF_NAVAID_TYPES:
            if ident in existing_vhf:
                vhf_updated += 1
            else:
                vhf_new += 1

            row_data = (
                nearest_apt,                     # airport_identifier
                area_code,                        # area_code
                None,                             # continent
                None,                             # country
                'WGE',                            # datum_code
                n['Elevation'],                   # dme_elevation
                None,                             # dme_ident (separate lookup)
                None,                             # dme_latitude
                None,                             # dme_longitude
                icao_code,                        # icao_code
                None,                             # ilsdme_bias
                n['MagneticVariation'],           # magnetic_variation
                navaid_class,                     # navaid_class
                freq,                             # navaid_frequency (MHz)
                ident,                            # navaid_identifier
                lat,                              # navaid_latitude
                lon,                              # navaid_longitude
                (n['Name'] or '')[:30],           # navaid_name
                n['Range'],                       # range
                n['SlavedVar'],                   # station_declination
            )
            vhf_rows.append(row_data)

        elif ntype in NDB_NAVAID_TYPES:
            if ident in existing_ndb:
                ndb_updated += 1
            else:
                ndb_new += 1

            row_data = (
                area_code,                        # area_code
                None,                             # continent
                None,                             # country
                'WGE',                            # datum_code
                icao_code,                        # icao_code
                n['MagneticVariation'],           # magnetic_variation
                navaid_class,                     # navaid_class
                freq,                             # navaid_frequency (KHz)
                ident,                            # navaid_identifier
                lat,                              # navaid_latitude
                lon,                              # navaid_longitude
                (n['Name'] or '')[:30],           # navaid_name
                n['Range'],                       # range
            )
            ndb_rows.append(row_data)

    print(f"  Fenix total navaids: {len(fenix_navaids)}")
    print(f"  频率越界被拒绝: {freq_rejected}")
    print(f"  VHF 导航台: 新增 {vhf_new}, 更新 {vhf_updated}")
    print(f"  NDB 导航台: 新增 {ndb_new}, 更新 {ndb_updated}")

    from db_utils import batch_merge_by_coordinates  # type: ignore[import-untyped]
    batch_merge_by_coordinates(
        dst_conn, 'tbl_d_vhfnavaids', TBL_D_COLUMNS, vhf_rows,
        'navaid_identifier', 'navaid_latitude', 'navaid_longitude',
    )
    batch_merge_by_coordinates(
        dst_conn, 'tbl_db_enroute_ndbnavaids', TBL_DB_COLUMNS, ndb_rows,
        'navaid_identifier', 'navaid_latitude', 'navaid_longitude',
    )

    # Build navaid lookup: NavaidID → {ident, lat, lon, type}
    navaid_lookup = {}
    for n in fenix_navaids:
        lat = n['Latitude']
        lon = n['Longtitude']
        if is_cn_airspace(lat, lon):
            navaid_lookup[n['ID']] = {
                'ident': (n['Ident'] or '').strip(),
                'lat': lat,
                'lon': lon,
                'type': str(n['Type']),
                'freq': decode_freq(n['Freq'], str(n['Type'])),
                'name': n['Name'],
            }

    return navaid_lookup
