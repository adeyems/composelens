import unittest
from composelens import differences, filter_changes

class IgnoreTest(unittest.TestCase):
    def test_exact_subtree_and_escaped_keys(self):
        changes = differences({'a/b': {'x': 1}, 'ab': 1}, {'a/b': {'x': 2}, 'ab': 2})
        kept, hidden, rules = filter_changes(changes, ['/a~1b', '/a~1b'])
        self.assertEqual(kept, [{'path': '/ab', 'kind': 'changed'}])
        self.assertEqual(hidden[0]['path'], '/a~1b/x')
        self.assertEqual(rules, ['/a~1b'])
    def test_ambiguous_and_invalid_rules(self):
        for rule in ['', 'a', '/bad~2', '/services/web/ports/0']:
            with self.assertRaises(ValueError): filter_changes([], [rule])
        change = {'path': '/one/value', 'baseline_path': '/two/value', 'kind': 'changed'}
        self.assertEqual(filter_changes([change], ['/one'])[0], [change])
    def test_cli_effective_rules_and_status(self):
        import io, json
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from composelens import main
        out = io.StringIO()
        with patch('composelens.resolve', side_effect=[({'services': {'web': {'image': 'a'}}}, False), ({'services': {'web': {'image': 'b'}}}, False)]), redirect_stdout(out):
            self.assertEqual(main(['-b', 'a', '-c', 'b', '--ignore', '/services/web/image']), 0)
        report = json.loads(out.getvalue())
        self.assertTrue(report['match'])
        self.assertEqual(len(report['suppressed_changes']), 1)
        self.assertEqual(report['summary']['services'][0]['status'], 'unchanged')
