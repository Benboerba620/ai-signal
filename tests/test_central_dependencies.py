import importlib.util
import unittest


class CentralDependencyContractTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("twscrape"),
        "central feed dependencies are not installed",
    )
    def test_twscrape_accepts_16_hex_legacy_chunk_hashes(self):
        from twscrape.xclid import get_scripts_list

        html = (
            '{100:"main",200:"shared~feature"}'
            '+{100:"15e48250ae23af9e",200:"00c0ffee00c0ffee"}'
        )

        self.assertEqual(
            get_scripts_list(html),
            [
                "https://abs.twimg.com/responsive-web/client-web/main.15e48250ae23af9ea.js",
                "https://abs.twimg.com/responsive-web/client-web/shared~feature.00c0ffee00c0ffeea.js",
            ],
        )


if __name__ == "__main__":
    unittest.main()
