"""Create the empty database. Run from the repo root: python scripts/init_db.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xangx.shared.db import connect, init_db  # noqa: E402


def main():
    conn = connect()
    try:
        init_db(conn)
        path = conn.execute("PRAGMA database_list").fetchone()["file"]
    finally:
        conn.close()
    print(f"Database ready: {path}")


if __name__ == "__main__":
    main()
