"""PyInstaller entry for the Parker engine sidecar.

The bundled binary IS the `parker` CLI: `parker serve` for the engine
server, `parker talk` for the voice loop, plus doctor/selftest/etc —
one binary, argv dispatch (see backend/parker.spec, `make sidecar`).
"""

import multiprocessing
import sys

from app.cli import main

if __name__ == "__main__":
    # Defensive: nothing forks workers today, but a frozen binary that
    # ever touches multiprocessing without this re-runs the whole CLI.
    multiprocessing.freeze_support()
    sys.exit(main())
