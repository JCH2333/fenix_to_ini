"""
Constants, mappings, and enumerations for Fenix → iniBuilds conversion.
"""

# ---- Chinese Airspace ICAO Filters ----
# Airport ICAO prefixes for Chinese airspace (as used by iFly-NDB reference)
CN_ICAO_PREFIXES = ('ZB', 'ZG', 'ZH', 'ZJ', 'ZL', 'ZP', 'ZS', 'ZU', 'ZW', 'ZY')
CN_SPECIAL_AIRPORTS = ('OPGT', 'VHHX')  # Gilgit (Pakistan), Old Kai Tak (HK)

# Some Chinese airports use an ARINC 424 ICAO region code that differs from
# the first two characters of the airport identifier. These stable mappings
# were cross-checked against the working iniBuilds 2604 reference database.
AIRPORT_ICAO_CODE_OVERRIDES = {
    'ZBAL': 'ZL', 'ZBAR': 'ZL', 'ZBCF': 'ZY', 'ZBEN': 'ZL',
    'ZBES': 'ZY', 'ZBHZ': 'ZY', 'ZBLA': 'ZY', 'ZBMZ': 'ZY',
    'ZBTL': 'ZY', 'ZBUH': 'ZL', 'ZBUL': 'ZY', 'ZBZL': 'ZY',
    'ZSFY': 'ZH', 'ZUAL': 'ZW', 'ZUJZ': 'ZP', 'ZULB': 'ZG',
    'ZUNP': 'ZG', 'ZUPL': 'ZW', 'ZUWS': 'ZH', 'ZUYI': 'ZP',
    'ZUZH': 'ZP',
}


def is_cn_airport(icao: str) -> bool:
    """Check if an ICAO code belongs to Chinese airspace."""
    if icao in CN_SPECIAL_AIRPORTS:
        return True
    return icao[:2] in CN_ICAO_PREFIXES


def get_airport_icao_code(icao: str) -> str:
    """Return the ARINC region code used by iniBuilds for an airport."""
    return AIRPORT_ICAO_CODE_OVERRIDES.get(icao, icao[:2])


# ---- Navaid Type Mapping ----
# Fenix type code → (iniBuilds target table, description, classification)
NAVAID_TYPE_MAP = {
    '1': ('vhf', 'VOR', 'VH'),
    '2': ('vhf', 'VORTAC', 'VH'),
    '3': ('vhf', 'TACAN', 'VH'),
    '4': ('vhf', 'VOR-DME', 'VH'),
    '5': ('ndb', 'NDB', 'N'),
    '7': ('ndb', 'NDB-DME', 'N'),
    '8': ('skip', 'ILS-DME', None),  # Handled by ILS table
    '9': ('vhf', 'DME', 'D'),
}

# VHF navaid types (go to tbl_d_vhfnavaids)
VHF_NAVAID_TYPES = {'1', '2', '3', '4', '9'}

# NDB navaid types (go to tbl_db_enroute_ndbnavaids)
NDB_NAVAID_TYPES = {'5', '7'}


def get_navaid_class(ntype: str, usage: str | None, navaid_range: int | None,
                     elevation: int | None) -> str:
    """
    Generate navaid_class code for iniBuilds format.

    Returns 5-character string like 'VDHW ', 'NDB  ', etc.
    Based on patterns observed in iniBuilds tbl_d_vhfnavaids.
    """
    if ntype in ('1', '2', '3', '4'):
        # VOR types
        if ntype in ('2', '4'):
            base = 'VD'
        else:
            base = 'VH'
        # Add usage-based suffix
        if usage == 'H':
            base += 'H'
        elif usage == 'B':
            base += 'H'
        else:
            base += 'L'
    elif ntype == '9':
        base = 'DME '
    elif ntype in ('5', '7'):
        if ntype == '7':
            base = 'NDB-D'
        else:
            base = 'NDB'
    else:
        base = 'UNKN'

    return base.ljust(5)[:5]


# ---- Terminal Procedure Mapping ----
# Fenix Terminals.Proc → iniBuilds target table
PROC_TO_TABLE = {
    '1': 'tbl_pe_stars',   # STAR
    '2': 'tbl_pd_sids',    # SID
    '3': 'tbl_pf_iaps',    # IAP (Approach)
}

# ---- Path Terminator (Leg Type) Mapping ----
# Fenix TerminalLegs.Type can be 1-2 characters
# Map to standard ARINC 424 2-character path terminators
PATH_TERMINATOR_MAP = {
    'AF': 'AF', 'CA': 'CA', 'CD': 'CD', 'CF': 'CF',
    'CI': 'CI', 'CR': 'CR', 'DF': 'DF', 'DS': 'DS',
    'FA': 'FA', 'FC': 'FC', 'FD': 'FD', 'FM': 'FM',
    'HA': 'HA', 'HF': 'HF', 'HM': 'HM', 'IF': 'IF',
    'PI': 'PI', 'RF': 'RF', 'TF': 'TF',
    'VA': 'VA', 'VD': 'VD', 'VI': 'VI', 'VM': 'VM', 'VR': 'VR',
}

# Fenix single-character leg types (observed in data: A, D, I, L, N, Q, R, S, T, V, etc.)
# These need contextual inference or direct passthrough
# Based on iFly-NDB reference and Fenix data patterns
FENIX_LEG_TYPE_CODES = {
    '0': 'IF',   # Initial Fix (numeric code used in some data)
    '1': 'TF',   # Track to Fix
    '2': 'CF',   # Course to Fix
    '3': 'DF',   # Direct to Fix
    '4': 'CA',   # Course to Altitude
    '5': 'VA',   # Heading to Altitude
    '6': 'VI',   # Heading to Intercept
    'A': 'CA',   # Course to Altitude (alt format)
    'B': 'CD',   # Course to DME Distance
    'D': 'DF',   # Direct to Fix
    'F': 'FC',   # Course from Fix to Distance
    'G': 'PI',   # Procedure Turn
    'I': 'IF',   # Initial Fix
    'J': 'RF',   # Radius to Fix
    'L': 'AF',   # Arc to Fix
    'N': 'TF',   # Track to Fix (variant)
    'P': 'PI',   # Procedure Turn
    'Q': 'RF',   # Radius to Fix (variant)
    'R': 'RF',   # Radius to Fix
    'S': 'CF',   # Course to Fix (variant)
    'T': 'TF',   # Track to Fix (variant)
    'V': 'VA',   # Heading to Altitude (variant)
    'X': 'DF',   # Direct to Fix (variant)
}


def map_path_terminator(fenix_type: str) -> str:
    """Map Fenix leg type code to standard 2-char ARINC 424 path terminator."""
    if fenix_type in PATH_TERMINATOR_MAP:
        return fenix_type
    if fenix_type in FENIX_LEG_TYPE_CODES:
        return FENIX_LEG_TYPE_CODES[fenix_type]
    return fenix_type.ljust(2)[:2]  # Fallback: pad to 2 chars


# ---- Surface Type Mapping ----
# Fenix Runways.Surface → iniBuilds surface_code
SURFACE_MAP = {
    'ASP': 'ASPH',
    'ASPH': 'ASPH',
    'BIT': 'ASPH',  # Bituminous → Asphalt
    'BRI': 'BRCK',
    'CLA': 'CLAY',
    'COM': 'CONC',
    'CON': 'CONC',
    'CONC': 'CONC',
    'COR': 'GRVL',  # Coral → Gravel
    'DIR': 'DIRT',
    'GRA': 'GRVL',
    'GRE': 'GRVL',  # Graded earth → Gravel
    'GRS': 'GRAS',
    'GRVL': 'GRVL',
    'ICE': 'ICE ',
    'LAT': 'LATE',
    'MAC': 'ASPH',  # Macadam → Asphalt
    'MAT': 'MATS',
    'MEM': 'ASPH',  # Membrane → Asphalt
    'OIL': 'ASPH',  # Oiled → Asphalt
    'PEM': 'ASPH',
    'PER': 'ASPH',  # Permanent → Asphalt
    'PSP': 'PSP ',  # Pierced steel plank
    'SAN': 'SAND',
    'SNO': 'SNOW',
    'TAR': 'ASPH',
    'UNK': 'UNKN',
    'UNPV': 'UNPV',  # Unpaved
    'WAT': 'WATE',
    'WOO': 'WOOD',
}


def map_surface(fenix_surface: str) -> str:
    """Map Fenix runway surface code to iniBuilds 4-char surface_code."""
    if fenix_surface is None:
        return 'UNKN'
    return SURFACE_MAP.get(fenix_surface.upper()[:3], 'UNKN')


# ---- Altitude Parsing ----
def parse_altitude(alt_text: str) -> tuple[int | None, int | None, str | None]:
    """
    Parse Fenix TerminalLegs.Alt field.

    Format examples:
        '06142A' → 6142 ft, At or Above
        '13500A' → 13500 ft, At or Above
        'FL120B' → FL120, At or Below
        'MAP'     → Missed Approach Point
        '' or None → no altitude constraint

    Returns: (altitude1, altitude2, altitude_description)
    """
    if not alt_text or not alt_text.strip():
        return None, None, None

    alt_text = alt_text.strip().upper()

    if alt_text == 'MAP':
        # MAP marks the missed-approach point; it is not an ARINC altitude
        # description.  DFDv2 only accepts a single-character constraint here.
        return None, None, None

    # Check for FL prefix
    is_fl = alt_text.startswith('FL')
    if is_fl:
        alt_text = alt_text[2:]

    # Extract numeric part and suffix
    numeric = ''
    suffix = ''
    for ch in alt_text:
        if ch.isdigit():
            numeric += ch
        elif ch in ('A', 'B', '+', '-', 'M'):
            suffix = ch
            break
        else:
            # Non-standard format
            break

    if not numeric:
        return None, None, None

    altitude = int(numeric)

    # Convert FL to feet if needed (FL120 = 12000 ft)
    if is_fl:
        altitude = altitude * 100

    # Map suffix to altitude_description
    desc_map = {
        'A': '+',   # At or Above
        'B': '-',   # At or Below
        '+': '+',   # At or Above
        '-': '-',   # At or Below
        'M': 'M',   # Mandatory
    }
    desc = desc_map.get(suffix, None)

    return altitude, None, desc
