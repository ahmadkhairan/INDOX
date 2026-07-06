from __future__ import annotations

import ast
import unittest
from pathlib import Path

from data.fetcher import get_sector_for_ticker


ROOT = Path("/Users/ahmadkhairan/Programming/INDOX")


class ImportGraphTests(unittest.TestCase):
    def test_core_paths_do_not_depend_on_market_data_facade(self):
        targets = [
            "data/fetcher.py",
            "cogs/analisis_cog.py",
            "cogs/market_cog.py",
            "cogs/picks_cog.py",
            "core/ai_engine.py",
        ]
        for rel_path in targets:
            source = (ROOT / rel_path).read_text(encoding="utf-8")
            tree = ast.parse(source, rel_path)
            imports = [
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            ]
            self.assertNotIn("utils.market_data", imports, rel_path)

    def test_data_fetcher_sector_lookup_uses_shared_sector_map(self):
        self.assertEqual(get_sector_for_ticker("BBCA"), "Banking")
        self.assertEqual(get_sector_for_ticker("UNKNOWN"), "General")


if __name__ == "__main__":
    unittest.main()
