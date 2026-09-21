import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
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

    def test_real_systemd_unit_validation(self):
        analyzer = shutil.which('systemd-analyze')
        if not analyzer:
            if os.environ.get('KOMARI_REQUIRE_SYSTEMD_VERIFY') == '1':
                self.fail('systemd-analyze is required for release validation')
            self.skipTest('systemd-analyze is unavailable on this host')
        root = Path(tempfile.mkdtemp(prefix='komari-systemd-verify-'))
        for directory in ['/opt/komari', '/opt/komari space/$literal%value']:
            unit = root/'komari-agent.service'
            # An existing executable isolates directive parsing from installation.
            unit.write_text(namespace['service_unit']('/bin/true', directory, legacy=True))
            legacy = subprocess.run([analyzer, 'verify', str(unit)], capture_output=True, text=True)
            self.assertNotEqual(legacy.returncode, 0, 'The legacy quoted path must reproduce the failure')
            unit.write_text(namespace['service_unit']('/bin/true', directory))
            fixed = subprocess.run([analyzer, 'verify', str(unit)], capture_output=True, text=True)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)

    def exercise_install(self, *, legacy=False, fail_step=None, retry=False,
                         wrong_token=False, upgrade=False, custom_unit=False):
        root = Path(tempfile.mkdtemp(prefix='komari-new-install-test-'))
        directory, units = root/'installation', root/'units'
        directory.mkdir()
        units.mkdir()
        binary, config, unit = directory/'agent', directory/'config.json', units/'komari-agent.service'
        extra = ['--interval', '5']
        command = ' '.join(namespace['unit_arg'](str(x)) for x in [binary, '--config', config, *extra])
        saved = {'endpoint': 'https://monitor.example.com', 'token': 'test-only-secret',
                 'disable_auto_update': False, 'disable_web_ssh': False, 'ignore_unsafe_cert': False}
        if legacy:
            binary.write_bytes(b'old binary')
            config.write_text(json.dumps(saved))
            unit.write_text(namespace['service_unit'](command, directory, legacy=True))
            if custom_unit:
                unit.write_text(unit.read_text() + '# custom service\n')
        original_unit = unit.read_bytes() if legacy else None
        original_config = config.read_bytes() if legacy else None
        new_binary = b'new verified binary'
        checksum = hashlib.sha256(new_binary).hexdigest()
        calls = []
        failed = False

        def download(request, **kwargs):
            if request.full_url.endswith('SHA256SUMS'):
                return io.BytesIO((checksum + '  komari-agent-linux-amd64\n').encode())
            return io.BytesIO(new_binary)

        def run(*args):
            nonlocal failed
            calls.append(args)
            output = ''
            if args[-1] == '--version':
                output = 'komari-agent version 1.2.62\n'
            elif '--property=FragmentPath' in args:
                output = str(unit) if unit.exists() else ''
            elif '--property=ExecStart' in args:
                output = '{ path=' + str(binary) + ' ; argv[]=agent --custom ; }'
            elif args[:2] in [('systemctl', 'start'), ('systemctl', 'stop')]:
                if unit.exists() and 'WorkingDirectory="' in unit.read_text():
                    raise subprocess.CalledProcessError(1, args, stderr='bad-setting')
            if len(args) > 1 and args[0] == 'systemctl' and args[1] == fail_step and not failed:
                failed = True
                raise subprocess.CalledProcessError(1, args, stderr='private test-only-secret')
            return subprocess.CompletedProcess(args, 0, stdout=output, stderr='')

        def safe_run(args, **kwargs):
            calls.append(tuple(args))
            return subprocess.CompletedProcess(args, 3 if args[1] == 'is-active' else 0, stdout='', stderr='')

        argv = ['install.sh', '--install-version', '1.2.62', '--install-dir', str(directory)]
        if not upgrade:
            argv += ['--endpoint', saved['endpoint'], '--token', 'different-test-token' if wrong_token else saved['token'],
                     '--disable-auto-update=false', *extra]
        errors = io.StringIO()
        with patch.dict(namespace, {'Path': lambda p: units if str(p) == '/etc/systemd/system' else Path(p), 'run': run}), \
             patch.object(namespace['sys'], 'argv', argv), \
             patch.object(namespace['platform'], 'system', return_value='Linux'), \
             patch.object(namespace['platform'], 'machine', return_value='x86_64'), \
             patch.object(namespace['urllib'].request, 'urlopen', side_effect=download), \
             patch.object(namespace['os'], 'geteuid', return_value=0), \
             patch.object(namespace['shutil'], 'which', return_value='/usr/bin/systemctl'), \
             patch.object(namespace['time'], 'sleep'), \
             patch.object(namespace['subprocess'], 'run', side_effect=safe_run), redirect_stderr(errors):
            if wrong_token or custom_unit:
                with self.assertRaises(ValueError):
                    namespace['main']()
                self.assertEqual(config.read_bytes(), original_config)
                self.assertEqual(unit.read_bytes(), original_unit)
                self.assertEqual(binary.read_bytes(), b'old binary')
                self.assertNotIn(('systemctl', 'stop', 'komari-agent.service'), calls)
                return
            if fail_step:
                with self.assertRaises(subprocess.CalledProcessError):
                    namespace['main']()
                self.assertIn('Installation failed during:', errors.getvalue())
                self.assertNotIn('test-only-secret', errors.getvalue())
                if legacy:
                    self.assertEqual(binary.read_bytes(), b'old binary')
                    self.assertEqual(unit.read_bytes(), original_unit)
                    self.assertEqual(config.read_bytes(), original_config)
                if not retry:
                    return
                retry_config = config.read_bytes()
                namespace['main']()
                self.assertEqual(config.read_bytes(), retry_config)
            else:
                namespace['main']()
            if retry and not fail_step:
                original = config.read_bytes()
                namespace['main']()
                self.assertEqual(config.read_bytes(), original)
        self.assertEqual(json.loads(config.read_text()), saved)
        self.assertEqual(binary.read_bytes(), new_binary)
        self.assertEqual(unit.read_text(), namespace['service_unit'](command, directory))
        if not legacy:
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        else:
            self.assertEqual(config.read_bytes(), original_config)
            self.assertTrue(any(p.read_bytes() == original_unit for p in directory.glob('backup-*/service.unit')))
        if not upgrade:
            self.assertIn(('systemctl', 'enable', 'komari-agent.service'), calls)

    def test_fresh_install_and_identical_retry(self):
        self.exercise_install(retry=True)

    def test_fresh_start_failure_can_be_retried(self):
        self.exercise_install(fail_step='start', retry=True)

    def test_active_check_failure_can_be_retried(self):
        self.exercise_install(fail_step='is-active', retry=True)

    def test_enable_failure_can_be_retried(self):
        self.exercise_install(fail_step='enable', retry=True)

    def test_legacy_failed_install_is_repaired_with_same_credentials(self):
        self.exercise_install(legacy=True)

    def test_legacy_failed_install_is_repaired_in_upgrade_mode(self):
        self.exercise_install(legacy=True, upgrade=True)

    def test_failed_legacy_repair_restores_binary_and_unit(self):
        self.exercise_install(legacy=True, fail_step='start')

    def test_different_credentials_never_overwrite_installation(self):
        self.exercise_install(legacy=True, wrong_token=True)

    def test_custom_unit_is_not_automatically_rewritten(self):
        self.exercise_install(legacy=True, custom_unit=True)

    def test_working_directory_is_literal(self):
        self.assertEqual(namespace['unit_directory']('/opt/komari space/$name%value'), '/opt/komari space/$name%%value')
        for value in ['/opt/test\nUser=other', '/opt/test\\', '/opt/test ', 'relative']:
            with self.assertRaises(ValueError):
                namespace['unit_directory'](value)

    def test_executable_does_not_use_argument_dollar_escaping(self):
        self.assertEqual(namespace['agent_command']('/opt/$dir/agent', '/opt/$dir/config.json'),
                         '"/opt/$dir/agent" "--config" "/opt/$$dir/config.json"')

if __name__ == '__main__':
    unittest.main()
