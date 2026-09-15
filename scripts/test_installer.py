import hashlib
from pathlib import Path
import tempfile
import unittest

namespace = {'__name__': 'installer_test'}
source = (Path(__file__).resolve().parents[1]/'install.sh').read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
exec(compile(source, 'install.sh', 'exec'), namespace)

class InstallerTests(unittest.TestCase):
    def test_rejects_corrupt_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary, sums = root/'agent', root/'SHA256SUMS'
            binary.write_bytes(b'valid binary')
            sums.write_text(hashlib.sha256(binary.read_bytes()).hexdigest() + '  komari-agent-linux-amd64\n')
            namespace['verify'](binary, sums, 'komari-agent-linux-amd64')
            binary.write_bytes(b'corrupted binary')
            with self.assertRaises(ValueError):
                namespace['verify'](binary, sums, 'komari-agent-linux-amd64')

    def test_rejects_ambiguous_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary, sums = root/'agent', root/'SHA256SUMS'
            binary.write_bytes(b'valid')
            line = hashlib.sha256(b'valid').hexdigest() + '  agent\n'
            sums.write_text(line * 2)
            with self.assertRaises(ValueError):
                namespace['verify'](binary, sums, 'agent')

    def test_service_arguments_remain_literal(self):
        self.assertEqual(namespace['unit_arg']('a$b%c"d'), '"a$$b%%c\\"d"')
        with self.assertRaises(ValueError):
            namespace['unit_arg']('a\nExecStart=bad')

if __name__ == '__main__':
    unittest.main()
