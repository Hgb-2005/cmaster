import subprocess
import unittest
from unittest.mock import patch
import judge


class CompilerDetectionTests(unittest.TestCase):
    def setUp(self):
        judge._compiler_cache.update(path=None, checked=False)

    def tearDown(self):
        judge._compiler_cache.update(path=None, checked=False)

    def test_install_after_negative_detection_is_seen(self):
        with patch.object(judge, '_candidates', return_value=iter([])):
            self.assertIsNone(judge.find_compiler())
        with patch.object(judge, '_candidates', return_value=iter(['new-gcc'])), patch('judge.os.path.isfile', return_value=True):
            self.assertEqual(judge.find_compiler(), 'new-gcc')

    def test_broken_executable_is_not_ready(self):
        with patch.object(judge, 'find_compiler', return_value='gcc'), patch('judge.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'missing dll')):
            self.assertFalse(judge.compiler_info()['ok'])
        with patch.object(judge, 'find_compiler', return_value='gcc'), patch('judge.subprocess.run', side_effect=OSError('bad executable')):
            self.assertFalse(judge.compiler_info()['ok'])

    def test_valid_compiler_reports_version(self):
        with patch.object(judge, 'find_compiler', return_value='gcc'), patch('judge.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'gcc 16\nCopyright', '')):
            self.assertEqual(judge.compiler_info()['version'], 'gcc 16')
