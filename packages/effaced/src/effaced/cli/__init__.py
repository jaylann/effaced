"""The ``effaced`` command-line entry point.

A thin stdlib-argparse wrapper over the completeness and reachability
linters, so ``effaced lint myapp.models:Base`` can run in CI without writing
a test harness. This subpackage is the console-script surface, not library
API — it is deliberately kept out of the root ``effaced`` ``__all__``.
"""

from effaced.cli.main import main

__all__ = ["main"]
