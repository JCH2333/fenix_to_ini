"""
Frequency encoding/decoding for Fenix navigation data.

Fenix stores frequencies as packed integers. The decoding algorithm:
1. Convert integer to uppercase hex string
2. Parse the hex digits as a decimal number
3. Divide by 10000 for VHF/ILS/DME (MHz), or 100 for NDB (KHz)
"""


def decode_freq(raw: int, navaid_type: str | None = None) -> float:
    """
    Decode Fenix packed integer frequency to REAL MHz or KHz.

    Args:
        raw: Raw integer frequency from Fenix DB
        navaid_type: Fenix navaid type code
            '1'=VOR, '2'=VORTAC, '3'=TACAN, '4'=VOR-DME, '9'=DME (VHF → MHz)
            '5'=NDB, '7'=NDB-DME (NDB → KHz)
            '8'=ILS-DME (ILS → MHz)
            None for ILS frequencies (treated as VHF/MHz)

    Returns:
        Decoded frequency as float (MHz for VHF/ILS, KHz for NDB)

    Examples:
        >>> decode_freq(18055168, '3')  # ZFX TACAN
        113.8
        >>> decode_freq(56623104, '5')  # AI NDB (hex 3600000 → 360.0 KHz)
        360.0
        >>> decode_freq(17903616, None)  # ISYK ILS
        111.3
    """
    if raw == 0:
        return 0.0

    hex_str = format(raw, 'X')
    decimal_value = int(hex_str, 10)

    # All frequencies use the same divisor: /10000
    # For VHF/ILS/DME: result is in MHz (e.g. 1138000 / 10000 = 113.8 MHz)
    # For NDB: result is in KHz (e.g. 3600000 / 10000 = 360.0 KHz)
    return decimal_value / 10000.0


def format_freq_display(freq: float, navaid_type: str | None = None) -> str:
    """Format decoded frequency for display."""
    if navaid_type in ('5', '7'):
        return f"{freq:.1f} KHz"
    else:
        return f"{freq:.2f} MHz"


def is_valid_frequency(freq: float, navaid_type: str | None) -> bool:
    """
    Check if decoded frequency is within valid range for navaid type.

    Args:
        freq: Decoded frequency (MHz for VHF, KHz for NDB)
        navaid_type: Fenix navaid type code

    Returns:
        True if frequency is within expected range
    """
    if navaid_type in ('5', '7'):
        # NDB: 190 - 1750 KHz
        return 190.0 <= freq <= 1750.0
    elif navaid_type in ('1', '2', '3', '4', '9'):
        # VHF/DME/TACAN: 108.00 - 117.95 MHz
        # Note: TACAN may use extended range or channel mapping
        return 108.0 <= freq <= 118.0
    elif navaid_type == '8' or navaid_type is None:
        # ILS: 108.00 - 111.95 MHz
        return 108.0 <= freq <= 112.0
    return True  # Unknown type, accept as valid
