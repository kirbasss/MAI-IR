from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import crawler as crawler_cli


class CrawlerCliTests(unittest.TestCase):
    def test_seed_only_does_not_download_pages(self) -> None:
        client = MagicMock()
        config = SimpleNamespace(db_uri="mongodb://unused", db_name="test")
        with (
            patch("sys.argv", ["crawler.py", "--seed-only", "crawler.large.yaml"]),
            patch.object(crawler_cli, "load_config", return_value=config),
            patch.object(crawler_cli, "MongoClient", return_value=client),
            patch.object(crawler_cli, "Crawler") as crawler_type,
            redirect_stdout(io.StringIO()),
        ):
            crawler_type.return_value.seed.return_value = 5
            result = crawler_cli.main()

        self.assertEqual(result, 0)
        crawler_type.return_value.ensure_indexes.assert_called_once_with()
        crawler_type.return_value.seed.assert_called_once_with()
        crawler_type.return_value.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
