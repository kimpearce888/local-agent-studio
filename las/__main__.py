"""``python -m las`` runs the studio CLI (host entry point)."""

import sys

from las.app.cli import main

if __name__ == "__main__":
    sys.exit(main())
