"""Rebuild the demo shop as of this morning. Run from the repo root: python scripts/reset_demo.py

Same reset as the director page, for when the server is closed. Database: XANGX_DB, else
data/xangx.sqlite. Files of data/inbox and data/outbox are moved to data/_prin-reset/, never deleted.
The data folder is XANGX_DATA_DIR, else data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xangx.demo.director import reset_demo  # noqa: E402
from xangx.shared.db import connect, init_db  # noqa: E402


def main():
    conn = connect()
    try:
        init_db(conn)
        result = reset_demo(conn)
    finally:
        conn.close()
    print(
        f"Το demo ξαναστήθηκε σε {result['ms'] / 1000:.1f} δευτ.: "
        f"{result['events']} γεγονότα, {result['moved_files']} αρχεία μπήκαν στην άκρη."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
