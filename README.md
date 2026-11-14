# Xangxothaika

An autonomous AI partner for small businesses. It watches the stock, the deliveries and the invoices, works out what needs doing, does what it is allowed to do on its own, and asks the owner only when a decision is needed.

The demo store is a 24-hour convenience store: a phone camera watches the shelf, a barcode scanner checks deliveries, and invoices are matched against the order, the delivery and the Greek tax platform (myDATA, test environment).

Built at the Gen AI Summit Hackathon, Athens, 14–15/11/2026.

**Status:** work in progress.

## Development

- Python 3.11, no dependencies (standard library only).
- Create the empty database: `python scripts/init_db.py`
- Run the tests: `python -m unittest discover -s tests -v`

All agents talk through an append-only event log (`xangx/shared/`).
