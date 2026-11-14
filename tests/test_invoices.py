import os
import unittest
import xml.etree.ElementTree as ET

from xangx.invoices import mydata


class BuildAndParse(unittest.TestCase):
    def test_build_invoice_totals(self):
        xml, gross = mydata.build_invoice_xml(
            "111111111", "222222222", "TEST", "1", "2026-11-14",
            [{"net_cents": 1000, "vat_rate": 24}, {"net_cents": 500, "vat_rate": 13}],
        )
        root = ET.fromstring(xml)
        self.assertEqual(gross, 1000 + 240 + 500 + 65)
        gross_el = [e for e in root.iter() if e.tag.endswith("totalGrossValue")][0]
        self.assertEqual(gross_el.text, "18.05")

    def test_parse_send_response(self):
        raw = ("<ResponseDoc><response><index>1</index><invoiceUid>U</invoiceUid>"
               "<invoiceMark>400001</invoiceMark><statusCode>Success</statusCode></response></ResponseDoc>")
        self.assertEqual(mydata.parse_send_response(raw),
                         [{"status": "Success", "mark": "400001", "uid": "U", "errors": []}])

    def test_parse_send_errors(self):
        raw = ("<ResponseDoc><response><index>1</index><statusCode>ValidationError</statusCode>"
               "<errors><error><message>bad</message><code>101</code></error></errors></response></ResponseDoc>")
        parsed = mydata.parse_send_response(raw)
        self.assertEqual(parsed[0]["errors"], [{"code": "101", "message": "bad"}])
        self.assertIsNone(parsed[0]["mark"])

    def test_parse_docs(self):
        raw = ('<RequestedDoc xmlns="http://www.aade.gr/myDATA/invoice/v1.0"><invoicesDoc><invoice>'
               "<uid>U</uid><mark>400001</mark><issuer><vatNumber>111111111</vatNumber></issuer>"
               "<invoiceHeader><series>A</series><aa>7</aa><issueDate>2026-11-14</issueDate></invoiceHeader>"
               "<invoiceSummary><totalNetValue>10.20</totalNetValue><totalGrossValue>12.65</totalGrossValue></invoiceSummary>"
               "</invoice></invoicesDoc></RequestedDoc>")
        docs = mydata.parse_docs(raw)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["mark"], "400001")
        self.assertEqual(docs[0]["issuer_afm"], "111111111")
        self.assertEqual(docs[0]["total_cents"], 1265)
        self.assertEqual(docs[0]["net_cents"], 1020)

    def test_missing_keys_is_clear_error(self):
        saved = {k: os.environ.pop(k, None) for k in ("MYDATA_USER_ID", "MYDATA_SUBSCRIPTION_KEY")}
        try:
            with self.assertRaises(mydata.MyDataError):
                mydata.credentials("store")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


_DOC = ('<RequestedDoc xmlns="http://www.aade.gr/myDATA/invoice/v1.0"><invoicesDoc><invoice>'
        "<mark>400001</mark><issuer><vatNumber>999877210</vatNumber></issuer>"
        "<invoiceHeader><series>A</series><aa>2271</aa></invoiceHeader>"
        "<invoiceSummary><totalGrossValue>74.04</totalGrossValue></invoiceSummary>"
        "</invoice></invoicesDoc></RequestedDoc>")

_KEYS = ("MYDATA_USER_ID", "MYDATA_SUBSCRIPTION_KEY",
         "MYDATA_SUPPLIER_USER_ID", "MYDATA_SUPPLIER_SUBSCRIPTION_KEY")


class CheckInvoice(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in _KEYS}
        self.real_call = mydata._call

    def tearDown(self):
        mydata._call = self.real_call
        for k in _KEYS:
            os.environ.pop(k, None)
            if self.saved[k] is not None:
                os.environ[k] = self.saved[k]

    def _store_keys(self):
        os.environ["MYDATA_USER_ID"] = "u"
        os.environ["MYDATA_SUBSCRIPTION_KEY"] = "k"

    def test_no_keys(self):
        r = mydata.check_invoice("999877210", "A", "2271", 7404)
        self.assertEqual(r["found"], False)
        self.assertTrue(r["error"])

    def test_found_and_totals_match(self):
        self._store_keys()
        mydata._call = lambda *a, **kw: (200, _DOC)
        r = mydata.check_invoice("999877210", "a", "2271", 7404)
        self.assertEqual(r, {"found": True, "totals_match": True, "mark": "400001", "error": None})

    def test_totals_differ(self):
        self._store_keys()
        mydata._call = lambda *a, **kw: (200, _DOC)
        r = mydata.check_invoice("999877210", "A", "2271", 7600)
        self.assertEqual(r["found"], True)
        self.assertEqual(r["totals_match"], False)

    def test_not_found(self):
        self._store_keys()
        mydata._call = lambda *a, **kw: (200, _DOC)
        r = mydata.check_invoice("999877210", "A", "9999", 7404)
        self.assertEqual(r["found"], False)
        self.assertIsNone(r["error"])

    def test_network_error_never_raises(self):
        self._store_keys()

        def boom(*a, **kw):
            raise mydata.MyDataError("myDATA unreachable: TimeoutError")
        mydata._call = boom
        r = mydata.check_invoice("999877210", "A", "2271", 7404)
        self.assertEqual(r["found"], False)
        self.assertIn("unreachable", r["error"])

    def test_garbage_reply_never_raises(self):
        self._store_keys()
        mydata._call = lambda *a, **kw: (200, "<not xml")
        r = mydata.check_invoice("999877210", "A", "2271", 7404)
        self.assertEqual(r["found"], False)
        self.assertTrue(r["error"])

    def test_http_error(self):
        self._store_keys()
        mydata._call = lambda *a, **kw: (401, "denied")
        r = mydata.check_invoice("999877210", "A", "2271", 7404)
        self.assertEqual(r["error"], "myDATA HTTP 401")


if __name__ == "__main__":
    unittest.main()
