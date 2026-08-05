import unittest

from mappings import get_airport_icao_code


class AirportIcaoCodeTests(unittest.TestCase):
    def test_uses_reference_confirmed_cross_region_codes(self):
        self.assertEqual(get_airport_icao_code("ZBCF"), "ZY")
        self.assertEqual(get_airport_icao_code("ZUAL"), "ZW")
        self.assertEqual(get_airport_icao_code("ZUJZ"), "ZP")
        self.assertEqual(get_airport_icao_code("ZUPL"), "ZW")

    def test_keeps_normal_airport_prefix(self):
        self.assertEqual(get_airport_icao_code("ZUNZ"), "ZU")
        self.assertEqual(get_airport_icao_code("ZBAA"), "ZB")


if __name__ == "__main__":
    unittest.main()
