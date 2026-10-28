"""Run the review suite and surface failures in GitHub Actions annotations."""

from __future__ import annotations

import io
import sys
import unittest


def main() -> int:
    output = io.StringIO()
    suite = unittest.defaultTestLoader.discover(".", pattern="test_*.py")
    result = unittest.TextTestRunner(stream=output, verbosity=1).run(suite)
    print(output.getvalue(), end="")
    for test, traceback in result.errors + result.failures:
        detail = "\n".join(traceback.splitlines()[-12:])
        detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title={test.id()}::{detail}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
