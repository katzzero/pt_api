"""Optional external native regressions, with no machine-specific paths."""
from pathlib import Path
import unittest
from pt_api import ProToolsSession


FIXTURES = Path(__file__).parent / 'fixtures'
CASES = [FIXTURES / ('external_audit_session_%d.ptx' % i) for i in (1, 2, 3)]


class TestExternalNativeSessions(unittest.TestCase):
    @unittest.skipUnless(CASES[0].exists(), 'Optional external native session 1 absent')
    def test_external_session_1_is_valid(self):
        ProToolsSession(str(CASES[0]))._validate_session_envelope()

    @unittest.skipUnless(CASES[1].exists(), 'Optional external native session 2 absent')
    def test_external_session_2_is_valid(self):
        ProToolsSession(str(CASES[1]))._validate_session_envelope()

    @unittest.skipUnless(CASES[2].exists(), 'Optional external native session 3 absent')
    def test_external_session_3_is_valid(self):
        ProToolsSession(str(CASES[2]))._validate_session_envelope()

if __name__ == '__main__':
    unittest.main()
