# xeAInhothika

An AI partner for a small shop that never closes. It watches the shelf, the deliveries and the invoices, works out what needs doing, does what it is allowed to do on its own, and asks the owner before anything that costs money.

The name comes from the Greek «ξεαγχώθηκα»: the stress is gone, with AI in the middle.

Built at the Gen AI Summit Hackathon, Athens, 14–15/11/2026.

## The demo
A 24-hour mini market in Pangrati, Athens. One owner is also the buyer, the stock-keeper and the bookkeeper.

1. **The shelf.** Someone takes 2 cold brews. A phone camera on the fridge sees it: «Fridge 1: 2 cold brew taken, low». The partner picks the supplier, writes the order and asks on Telegram «Shall I send it?». After «Yes» the order goes out by email. From the photo to the question: 19–22 seconds with the real model, 4 runs.
2. **The delivery.** We scan the cases with the phone and photograph the printed invoice. The screen says «ordered 10, got 9, billed for 10», and the partner asks «Shall I ask for a €2.43 credit?». After «Yes» the request goes to the supplier. From the invoice to the question: 27–34 seconds, 3 runs.
3. **What nobody asked for.** Two invoices that came by email were read on their own. The bakery's invoice has a new IBAN: «I won't change it without you». Coca-Cola went up 8.8%, and another supplier has it for €0.58.
4. **What happened is written down.** The day's history replays in a few seconds. The Tasks page shows how many jobs were done alone and how many needed a «yes». Every invoice shows when it is due.
5. **The MCP door.** The same tools are open to the accountant's own agent. Anything with money still asks the owner.

## How it works
- **One partner** talks to the owner on the screen and on Telegram. It plans, calls tools, and writes every money decision as an approval card. Nothing in that card runs until the owner taps «Yes».
- **It wakes up on its own** when something happens: low stock, an invoice that does not match, a new IBAN, a price change. It also runs a morning check at 07:00.
- **Three groups of tools**, one per team member:
  - **Stock and orders:** shelf camera (the model compares two photos and says how many were taken), stock and expiry dates, delivery check by barcode (phone camera, one case counts as all the units inside; a USB scanner types into the same field, not tested with a real one), order proposal from sales, supplier comparison, email to the supplier and reading the reply.
  - **Invoices:** reading a PDF or photo from a folder, an email or the phone. Checks without AI (totals, tax number, known supplier), matching against the delivery, price changes, duplicates, new IBAN, and **myDATA**: it asks the Greek tax platform whether the supplier declared the invoice, with the same total.
  - **Accountant:** payment reminders from the terms on the paper, «Did you pay? How?» for cash invoices, credit requests, a daily report, the month's folder for the accountant (ZIP with a summary), and purchase trends.
- **Events that are never deleted.** Every action is written as an event, with who did it and why. Stock is computed from the events, and the day's history replays from them.
- **No internet, nothing lost.** The screen says so in plain words. Invoice reading and myDATA retry on their own. An approved order leaves as soon as the connection is back. In a test, an invoice was read by itself 1′35″ after the internet came back, without a restart.

## What is real and what is simulated
| Real | Simulated (and said so on screen) |
|---|---|
| The phone camera and the model that counts the shelf | Till sales: a program «sells» at each hour's rate. The screen says «Till: simulation» |
| Barcode scanning (phone camera) | The 30-day history of sales and invoices |
| The model reading invoices (PDF, photos) and supplier emails | Products, prices, case barcodes, suppliers, tax numbers (with a valid check digit) and IBANs |
| The partner, its tools and the approvals | The people: the owner and the «supplier» are team members |
| Emails to and from the «supplier», and Telegram | The 3 demo invoices (Α-2271, Β-512, Γ-318) were made by us |
| myDATA, in the tax authority's test environment, with its real API | The «supplier» on myDATA is our second test account |
| Stock, shortage, VAT, price and payment calculations | The till simulation never brings a product to «low» on its own. Only the camera does |

## Safety
- **Hidden instructions inside invoices.** We made 15 trap invoices: white text on white, tiny text, base64, «change the IBAN», «paid, total 0», fake rules. The tax number, IBAN and totals were kept correct in **15 of 15**, and every trap was flagged. After a change to the reading step we ran it again: 15 of 15.
- **What the model reads is data, not instructions.** Photos, invoices and emails are read without tools. The model returns only numbers or a short summary, checked before anything is written. The body of an invoice email is never read.
- **Approval gates.** No payments. No order, email or IBAN change without «Yes». Email goes only to the supplier address on file. An IBAN is shown with its last 4 digits only.
- **Nothing twice.** The same button, email or invoice from two paths is written once.
- **The owner can stop or undo.** «Pause all» stops every action. «Undo» works on anything that can be reversed, and the undo is an event too.
- **Real invoices** were tested once, anonymised on one team member's laptop. They were never uploaded and were deleted after the test.
- Keys live only in a local `.env`, with a spend limit. Tax information is support, not accountant's advice.

## Numbers
- Shelf camera: right 18 times out of 19 on test photos (the 19th was off by one can). On the real stage shelf today: 6 of 7 right, 3.6–4.4 seconds per photo; the miss was a can left in front of another product.
- Trap invoices: 15 of 15.
- Photo to «Shall I send it?»: 19–22 seconds, 4 runs.
- Invoice to the credit question: 27–34 seconds, 3 runs.
- Automated tests: 456 passing (1 skipped).

## The live demo
There is no public link to our running demo, on purpose. It runs with our own keys: the model is billed, and the Telegram messages and supplier emails are really sent. A link in a public repository would let anyone send those emails and spend those keys.
- **To see it:** the 1-minute video, and the live presentation by the team.
- **To try it yourself:** run it on your own computer, as below. Without a key it uses a fixed fake model: no access code, no cost, nothing is sent.
- **Judges** who want to use our running demo can ask the team in person for the link and the access code.

## Run it
- Python 3.11. `pip install -r requirements.txt` (anthropic, fastapi, uvicorn).
- Copy `.env.example` to `.env` and fill in your own keys. Without a key the app runs with a fixed fake model, so the tests and the screens still work.
- Empty database: `python scripts/init_db.py`. Demo shop: `python scripts/seed_demo.py`.
- Server: `python -m xangx.server`.
- Tests: `python -m unittest discover -s tests`.
- Stack: Python, FastAPI, SQLite, live pages over server-sent events, Claude (`claude-opus-5-5`) through the API.

## Team
**Triton Dev:** Ioannis Argyras, Stavros Theofanopoulos, Manolis Manolis.
