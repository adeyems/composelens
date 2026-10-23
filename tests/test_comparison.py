import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from composelens import differences, main, resolve


class ComparisonTest(unittest.TestCase):
    def test_object_order_and_nested_changes(self):
        self.assertEqual(differences({'a': 1, 'b': 2}, {'b': 2, 'a': 1}), [])
        self.assertEqual(differences({'services': {'a': {'image': 'old'}}}, {'services': {'a': {'image': 'new'}}}),
                         [{'path': '/services/a/image', 'kind': 'changed'}])

    def test_add_remove_escape_and_no_values(self):
        result = differences({'a/b': {'~old': 'secret'}}, {'a/b': {'new': 'other-secret'}})
        self.assertEqual(result, [{'path': '/a~1b/new', 'kind': 'added'}, {'path': '/a~1b/~0old', 'kind': 'removed'}])
        self.assertNotIn('secret', json.dumps(result))

    def test_types_lists_and_null(self):
        self.assertTrue(differences(True, 1))
        self.assertTrue(differences(None, ''))
        self.assertEqual(differences([1], [1, 2]), [{'path': '/1', 'kind': 'added'}])
        self.assertEqual(differences([1, 2], [1]), [{'path': '/1', 'kind': 'removed'}])
        self.assertEqual(len(differences([1, 2], [2, 1])), 2)

    @patch('composelens.subprocess.run')
    def test_resolver_arguments_and_warning(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '{"services":{}}', 'warning')
        model, warning = resolve(['examples/base.yaml', 'examples/override.yaml'], '.')
        self.assertTrue(warning)
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ['--format', 'json'])
        self.assertEqual([command[i+1] for i, x in enumerate(command) if x == '-f'],
                         [str(Path(p).resolve()) for p in ['examples/base.yaml', 'examples/override.yaml']])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertNotIn('shell', run.call_args.kwargs)

    @patch('composelens.subprocess.run')
    def test_resolver_failures_hide_raw_output(self, run):
        for result in [subprocess.CompletedProcess([], 1, '', 'secret-password'),
                       subprocess.CompletedProcess([], 0, 'secret-password', ''),
                       subprocess.CompletedProcess([], 0, '[]', '')]:
            run.return_value = result
            with self.assertRaises(ValueError) as caught:
                resolve(['examples/base.yaml'], '.')
            self.assertNotIn('secret-password', str(caught.exception))

    @patch('composelens.subprocess.run')
    def test_timeout_and_missing_docker(self, run):
        for error in [FileNotFoundError(), subprocess.TimeoutExpired('docker', 30)]:
            run.side_effect = error
            with self.assertRaises(ValueError):
                resolve(['examples/base.yaml'], '.')

    def test_missing_file(self):
        with self.assertRaises(OSError):
            resolve(['examples/absent.yaml'], '.')

    @patch('composelens.resolve')
    def test_cli_codes_and_error_stream(self, resolver):
        for models, code in [([({'services': {}}, False)] * 2, 0),
                             ([({'services': {}}, False), ({'services': {'new': {}}}, False)], 1)]:
            resolver.side_effect = models
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['-b', 'a', '-c', 'b']), code)
            self.assertEqual(json.loads(out.getvalue())['match'], code == 0)
        resolver.side_effect = ValueError('invalid input')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(['-b', 'a', '-c', 'b']), 2)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('error', json.loads(err.getvalue()))


class ComposeIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        available = shutil.which('docker') and subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode == 0
        if not available:
            if os.environ.get('COMPOSELENS_REQUIRE_DOCKER') == '1':
                raise RuntimeError('CI requires Docker Compose integration tests')
            raise unittest.SkipTest('Docker Compose unavailable')

    def test_real_merge_and_cli(self):
        command = [sys.executable, 'composelens.py', '-b', 'examples/base.yaml', '-c', 'examples/base.yaml']
        same = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(same.returncode, 0, same.stderr)
        changed = subprocess.run(command + ['-c', 'examples/override.yaml'], capture_output=True, text=True)
        self.assertEqual(changed.returncode, 1, changed.stderr)
        self.assertEqual(json.loads(changed.stdout)['changes'],
                         [{'path': '/services/web/environment/MODE', 'kind': 'changed'},
                          {'path': '/services/web/image', 'kind': 'changed'}])

    def test_real_normalization_interpolation_and_invalid_input(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder) / 'first.yaml', Path(folder) / 'second.yaml'
            first.write_text('services:\n  web:\n    image: "${COMPOSELENS_TEST_IMAGE}"\n    environment: ["MODE=demo"]\n')
            second.write_text('services:\n  web:\n    image: nginx:1.27\n    environment:\n      MODE: demo\n')
            with patch.dict(os.environ, {'COMPOSELENS_TEST_IMAGE': 'nginx:1.27'}):
                a, _ = resolve([first], folder)
                b, _ = resolve([second], folder)
            self.assertEqual(differences(a, b), [])
            second.write_text('services: [invalid]\n')
            with self.assertRaises(ValueError):
                resolve([second], folder)
