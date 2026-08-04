import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.integrations.model_catalog import catalog_experiments


class ModelCatalogTests(unittest.TestCase):
    def test_catalogs_json_without_executing_code(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one.json").write_text(json.dumps({"name": "one", "total_return": 0.1}), encoding="utf-8")
            result = catalog_experiments(root)
            self.assertEqual(result["entries"][0]["experiment_name"], "one")


if __name__ == "__main__":
    unittest.main()
