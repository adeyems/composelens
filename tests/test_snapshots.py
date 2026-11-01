import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from composelens import load_snapshot, save_snapshot, main

class SnapshotTest(unittest.TestCase):
    def test_roundtrip_no_docker_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'snapshot.json'
            save_snapshot(path,{'services':{'web':{'environment':{'SYNTHETIC':'demo'}}}},False,{'files':['demo.yml']})
            self.assertEqual(load_snapshot(path)['provenance']['files'],['demo.yml'])
            with self.assertRaises(FileExistsError): save_snapshot(path,{},False,{})
            if os.name=='posix': self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with patch('composelens.resolve',side_effect=AssertionError('must not resolve')), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['--baseline-snapshot',str(path),'--candidate-snapshot',str(path)]),0)
            self.assertTrue(json.loads(out.getvalue())['match'])
    def test_invalid_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            for value in ['{}','{"version":1,"version":2}','not-json']:
                path.write_text(value)
                with self.assertRaises(ValueError): load_snapshot(path)
