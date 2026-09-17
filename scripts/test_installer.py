import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

namespace = {'__name__': 'installer_test'}
source = (Path(__file__).resolve().parents[1]/'install.sh').read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
exec(compile(source, 'install.sh', 'exec'), namespace)

@contextmanager
def retained_directory():
    # Keep test files for inspection; no automatic recursive removal.
    yield tempfile.mkdtemp(prefix='komari-installer-test-')

class InstallerTests(unittest.TestCase):
    def test_fork_distribution_sources(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(namespace['REPOSITORY'], 'mghts/komari-agent')
        self.assertIn('"mghts/komari-agent"', (root/'update/update.go').read_text())
        windows = (root/'install.ps1').read_text()
        self.assertIn('exit 1', windows)
        self.assertNotIn('Invoke-WebRequest', windows)
        self.assertNotIn('komari-monitor', windows)

    def test_rejects_corrupt_download(self):
        with retained_directory() as temporary:
            root = Path(temporary)
            binary, sums = root/'agent', root/'SHA256SUMS'
            binary.write_bytes(b'valid binary')
            sums.write_text(hashlib.sha256(binary.read_bytes()).hexdigest() + '  komari-agent-linux-amd64\n')
            namespace['verify'](binary, sums, 'komari-agent-linux-amd64')
            binary.write_bytes(b'corrupted binary')
            with self.assertRaises(ValueError):
                namespace['verify'](binary, sums, 'komari-agent-linux-amd64')

    def test_rejects_ambiguous_checksum(self):
        with retained_directory() as temporary:
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

    def exercise_upgrade(self, *, corrupt=False, fail_start=False):
        root = Path(tempfile.mkdtemp(prefix='komari-upgrade-test-'))
        directory, units = root/'installation', root/'units'
        directory.mkdir()
        units.mkdir()
        binary, config, unit = directory/'agent', directory/'config.json', units/'komari-agent.service'
        binary.write_bytes(b'old binary')
        config.write_text('{"token":"test-only","disable_auto_update":true}')
        unit.write_text('original unit with custom arguments')
        original_config, original_unit = config.read_bytes(), unit.read_bytes()
        new_binary = b'new verified binary'
        checksum = hashlib.sha256(new_binary).hexdigest()
        calls = []
        subprocess = namespace['subprocess']

        def download(request, **kwargs):
            if request.full_url.endswith('SHA256SUMS'):
                return io.BytesIO((checksum+'  komari-agent-linux-amd64\n').encode())
            return io.BytesIO(b'corrupted' if corrupt else new_binary)

        def command(*args):
            calls.append(args)
            output = ''
            if args[-1] == '--version':
                output = 'komari-agent version 1.2.61-rc.1\n'
            elif '--property=FragmentPath' in args:
                output = str(unit)
            elif '--property=ExecStart' in args:
                output = '{ path='+str(binary)+' ; argv[]=agent --custom ; }'
            elif args[:2] == ('systemctl', 'start') and binary.read_bytes() == new_binary and fail_start:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout=output, stderr='')

        def safe_run(args, **kwargs):
            calls.append(tuple(args))
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

        def mapped_path(value):
            return units if str(value) == '/etc/systemd/system' else Path(value)

        with patch.dict(namespace, {'Path': mapped_path, 'run': command}), \
             patch.object(namespace['sys'], 'argv', ['install.sh', '--install-version', '1.2.61-rc.1', '--install-dir', str(directory)]), \
             patch.object(namespace['platform'], 'system', return_value='Linux'), \
             patch.object(namespace['platform'], 'machine', return_value='x86_64'), \
             patch.object(namespace['urllib'].request, 'urlopen', side_effect=download), \
             patch.object(namespace['os'], 'geteuid', return_value=0), \
             patch.object(namespace['shutil'], 'which', return_value='/usr/bin/systemctl'), \
             patch.object(namespace['time'], 'sleep'), \
             patch.object(subprocess, 'run', side_effect=safe_run):
            if corrupt:
                with self.assertRaises(ValueError):
                    namespace['main']()
            elif fail_start:
                with self.assertRaises(subprocess.CalledProcessError):
                    namespace['main']()
            else:
                namespace['main']()
        self.assertEqual(config.read_bytes(), original_config)
        self.assertEqual(unit.read_bytes(), original_unit)
        self.assertEqual(binary.read_bytes(), b'old binary' if corrupt or fail_start else new_binary)
        if corrupt:
            self.assertFalse(calls, 'Unverified downloads must never touch the service')
        else:
            backups = list(directory.glob('backup-*/agent'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b'old binary')
            self.assertIn(('systemctl', 'stop', 'komari-agent.service'), calls)
            if fail_start:
                self.assertEqual(calls.count(('systemctl', 'start', 'komari-agent.service')), 2)

    def test_upgrade_preserves_existing_configuration(self):
        self.exercise_upgrade()

    def test_start_failure_restores_previous_binary(self):
        self.exercise_upgrade(fail_start=True)

    def test_corrupt_upgrade_does_not_stop_service(self):
        self.exercise_upgrade(corrupt=True)

if __name__ == '__main__':
    unittest.main()
