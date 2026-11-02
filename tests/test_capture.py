import os
import subprocess
import sys
import unittest
from composelens import capture_resolution
class CaptureResolutionTest(unittest.TestCase):
    def run_child(self, code):
        return capture_resolution([sys.executable,'-c',code],dict(os.environ))
    def test_separate_outputs_and_nonzero_exit(self):
        result=self.run_child('import sys; print("output"); print("diagnostic",file=sys.stderr); sys.exit(3)')
        self.assertEqual(result.returncode,3)
        self.assertEqual(result.stdout,'output\n')
        self.assertEqual(result.stderr,'diagnostic\n')
    def test_oversized_streams_and_invalid_utf8_rejected(self):
        for code in ('import sys; sys.stdout.write("x"*(8*1024*1024+1))','import sys; sys.stderr.write("x"*65537)','import sys; sys.stdout.buffer.write(b"\\xff")'):
            with self.assertRaises(ValueError):self.run_child(code)
    def test_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            capture_resolution([sys.executable,'-c','import time; time.sleep(10)'],dict(os.environ),timeout=.05)
