from __future__ import annotations

# Python components only. Native tokenizer tests: docker compose run --rm tokenizer-tests
import sys
import unittest


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
