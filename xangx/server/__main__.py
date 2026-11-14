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

    # Each role may run background work in `xangx/<role>/worker.py`, exposing
    # `start(db_path=None)` that starts daemon threads and returns at once.
    import importlib

    for module in ("xangx.stock.worker", "xangx.orders.worker",
                   "xangx.invoices.worker", "xangx.accountant.worker"):
        try:
            mod = importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name and module.startswith(exc.name):
                continue
            raise
        mod.start()

    port = int(os.environ.get("XANGX_PORT") or 8000)
    uvicorn.run("xangx.server.app:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
