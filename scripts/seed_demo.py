"""Fill the demo database. Run from the repo root: python scripts/seed_demo.py [--reset]

--reset deletes ONLY the demo database file (XANGX_DB, else data/xangx.sqlite)
and builds it again from scratch.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xangx.demo.director import start_cursors_at_end  # noqa: E402
from xangx.demo.seed import seed_all  # noqa: E402
from xangx.shared.db import DEFAULT_DB_PATH, connect, init_db  # noqa: E402


def main():
    path = os.environ.get("XANGX_DB") or DEFAULT_DB_PATH
    if "--reset" in sys.argv[1:] and path != ":memory:":
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass
    started = time.time()
    conn = connect(path)
    try:
        init_db(conn)
        if conn.execute("SELECT 1 FROM events LIMIT 1").fetchone():
            print(
                "Η βάση έχει ήδη δεδομένα. Τρέξε με --reset για να ξαναφτιαχτεί από την αρχή."
            )
            return 1
        seed_all(conn)
        start_cursors_at_end(conn)
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    print(f"Έτοιμο: {count} γεγονότα στο {path} ({time.time() - started:.1f} δευτ.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
