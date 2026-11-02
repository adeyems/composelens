import json
import contextlib
import io
import unittest
from unittest.mock import patch
from composelens import encode_report, main

class ReportBudgetTest(unittest.TestCase):
    def test_size_boundary_and_escaped_content(self):
        report={'path':'/services/é','changes':[]}
        encoded=encode_report(report)
        self.assertEqual(json.loads(encoded),report)
        with patch('composelens.MAX_REPORT_BYTES',len(encoded)):
            self.assertEqual(encode_report(report),encoded)
        with patch('composelens.MAX_REPORT_BYTES',len(encoded)-1):
            with self.assertRaisesRegex(ValueError,'no partial'):encode_report(report)

    def test_cli_emits_only_error_when_report_is_too_large(self):
        with patch('composelens.resolve', return_value=({'services':{}},False)), patch('composelens.MAX_REPORT_BYTES',10), contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['-b','unused','-c','unused']),2)
        self.assertEqual(out.getvalue(),'')
        self.assertIn('no partial',json.loads(err.getvalue())['error'])
