"""python -m xangx.server: start the server on 127.0.0.1 only."""

import os

import uvicorn


def main():
    try:
        from xangx.shared.env import load_env

        load_env()
    except ImportError:
        pass

    from xangx.shared.db import connect, init_db

    conn = connect()
    init_db(conn)
    conn.close()

    try:
        from xangx.master.agent import start_background

        start_background()
    except ImportError:
        print("xangx.master is not there yet: chat is off.")

    port = int(os.environ.get("XANGX_PORT") or 8000)
    uvicorn.run("xangx.server.app:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
