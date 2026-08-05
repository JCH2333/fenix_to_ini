import tempfile
import unittest
from pathlib import Path

from naip_metadata import NaipProcedureMetadata


class NaipProcedureMetadataTests(unittest.TestCase):
    def test_matches_rnp_ar_runway_and_named_variant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            terminal_dir = Path(temp_dir) / "Terminal"
            (terminal_dir / "ZPCW").mkdir(parents=True)
            (terminal_dir / "ZUNZ").mkdir(parents=True)
            (terminal_dir / "ZGSZ").mkdir(parents=True)
            (terminal_dir / "ZWKN").mkdir(parents=True)
            (terminal_dir / "ZLGL").mkdir(parents=True)
            (terminal_dir / "ZPCW" / "Charts.csv").write_text(
                "ChartName,PAGE_NUMBER,ChartTypeEx_CH,IS_SUP,IsModify,\n"
                "RNP z RWY23,5P-2,进近图_RNAV_RNP_RADAR_GPS_GNSS,False,False,\n"
                "RNP y RWY23(AR),5R-2,进近图_RNAV_RNP_RADAR_GPS_GNSS,False,False,\n",
                encoding="gb18030",
            )
            (terminal_dir / "ZUNZ" / "Charts.csv").write_text(
                "ChartName,PAGE_NUMBER,ChartTypeEx_CH,IS_SUP,IsModify,\n"
                "RNP RWY05(AR)(DUMIX),9A,进近图_RNAV_RNP_RADAR_GPS_GNSS,False,False,\n",
                encoding="gb18030",
            )
            (terminal_dir / "ZGSZ" / "Charts.csv").write_text(
                "ChartName,PAGE_NUMBER,ChartTypeEx_CH,IS_SUP,IsModify,\n"
                "RNP ILS/DME w RWY34R(AR),5L-13,仪表进近图_ILS,False,False,\n",
                encoding="gb18030",
            )
            (terminal_dir / "ZWKN" / "Charts.csv").write_text(
                "ChartName,PAGE_NUMBER,ChartTypeEx_CH,IS_SUP,IsModify,\n"
                "RNP y RWY30(AR),9D,进近图_RNAV_RNP_RADAR_GPS_GNSS,False,False,\n",
                encoding="gb18030",
            )
            (terminal_dir / "ZLGL" / "Charts.csv").write_text(
                "ChartName,PAGE_NUMBER,ChartTypeEx_CH,IS_SUP,IsModify,\n"
                "RNP(AR) ILS/DME z RWY30,5A,仪表进近图_ILS,False,False,\n",
                encoding="gb18030",
            )

            metadata = NaipProcedureMetadata(temp_dir)

            self.assertTrue(metadata.is_rnp_ar("ZPCW", "23", "R23-Y"))
            self.assertFalse(metadata.is_rnp_ar("ZPCW", "23", "R23-Z"))
            self.assertTrue(metadata.is_rnp_ar("ZUNZ", "05", "R05"))
            self.assertFalse(metadata.is_rnp_ar("ZUNZ", "23", "R23"))
            self.assertTrue(
                metadata.is_rnp_ar("ZGSZ", "34R", "I34RW", has_ils=True)
            )
            self.assertTrue(
                metadata.is_rnp_ar("ZWKN", "30", "I30-Y", has_ils=False)
            )
            self.assertTrue(
                metadata.is_rnp_ar("ZLGL", "30", "I30-Z", has_ils=False)
            )


if __name__ == "__main__":
    unittest.main()
