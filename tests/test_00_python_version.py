import sys
import unittest


class PythonVersionTest(unittest.TestCase):
    def test_python_is_at_least_3_11(self):
        # Many tests use hashlib.file_digest (3.11+). On older Python they fail
        # with AttributeError, which looks like real bugs.
        self.assertGreaterEqual(
            sys.version_info[:2], (3, 11),
            f"Python {sys.version.split()[0]} is too old: run the tests with Python 3.11, "
            "as CI does. Fix: `uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python "
            "-r requirements-central.txt && .venv/bin/python -m unittest discover -s tests`. "
            "Do not change the failing tests.")


if __name__ == "__main__":
    unittest.main()
