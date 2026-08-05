"""Read procedure semantics that are only present in the NAIP chart index."""

import csv
import re
from pathlib import Path


_RUNWAY_RE = re.compile(r"RWY\s*([0-9]{1,2}[LRC]?)", re.IGNORECASE)
_VARIANT_RE = re.compile(r"\b([WXYZ])\s+RWY", re.IGNORECASE)
_PROCEDURE_VARIANT_RE = re.compile(r"-([WXYZ])$", re.IGNORECASE)


class NaipProcedureMetadata:
    """Index RNP AR approaches from per-airport ``Charts.csv`` files."""

    def __init__(self, data_root: str | Path | None = None):
        self._rnp_ar_rules: dict[tuple[str, str, str], set[str]] = {}
        root = Path(data_root) if data_root else self._auto_detect_data_root()
        self.data_root = root if root and root.is_dir() else None
        if self.data_root:
            self._load_chart_indexes(self.data_root / "Terminal")

    @staticmethod
    def _auto_detect_data_root() -> Path | None:
        project_parent = Path(__file__).resolve().parent.parent
        candidates = (
            project_parent / "2607",
            Path.cwd() / "2607",
            Path.cwd().parent / "2607",
        )
        for candidate in candidates:
            if (candidate / "Terminal").is_dir():
                return candidate
        return None

    def _load_chart_indexes(self, terminal_root: Path) -> None:
        if not terminal_root.is_dir():
            return
        for chart_path in terminal_root.glob("*/Charts.csv"):
            airport = chart_path.parent.name.upper()
            try:
                with chart_path.open(
                    "r", encoding="gb18030", errors="replace", newline=""
                ) as chart_file:
                    for row in csv.DictReader(chart_file):
                        self._add_chart_rule(airport, row)
            except OSError:
                continue

    def _add_chart_rule(self, airport: str, row: dict[str, str]) -> None:
        name = (row.get("ChartName") or "").strip().upper()
        page = (row.get("PAGE_NUMBER") or "").strip().upper()
        if "RNP" not in name or "(AR)" not in name or not page.startswith(("5", "9")):
            return
        runway_match = _RUNWAY_RE.search(name)
        if not runway_match:
            return
        runway = runway_match.group(1).zfill(2)
        variant_match = _VARIANT_RE.search(name)
        variant = variant_match.group(1).upper() if variant_match else ""
        family = "I" if "ILS" in name else "G" if "GLS" in name else "R"
        self._rnp_ar_rules.setdefault((airport, runway, family), set()).add(variant)

    def is_rnp_ar(self, airport: str, runway: str,
                  procedure_identifier: str, has_ils: bool = False) -> bool:
        airport = (airport or "").strip().upper()
        runway = (runway or "").strip().upper().removeprefix("RW").zfill(2)
        procedure = (procedure_identifier or "").strip().upper()
        if not procedure:
            return False
        if procedure[0] == 'I' and not has_ils:
            families = ('I', 'R')
        else:
            families = ('I' if has_ils else procedure[0],)
        variants = set().union(*(
            self._rnp_ar_rules.get((airport, runway, family), set())
            for family in families
        ))
        match = _PROCEDURE_VARIANT_RE.search(procedure)
        procedure_variant = match.group(1).upper() if match else ""
        if not procedure_variant and procedure[-1:] in {'W', 'X', 'Y', 'Z'}:
            embedded_runway = procedure[1:-1].replace('-', '')
            if embedded_runway == runway:
                procedure_variant = procedure[-1]
        return procedure_variant in variants
