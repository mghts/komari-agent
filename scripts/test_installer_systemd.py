"""Exercise the installer against real systemd using isolated, retained test files.

Run as root on a Linux systemd host. Downloads use an inert test executable;
systemctl, unit loading, process startup and rollback are not mocked.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

namespace = {'__name__': 'installer_systemd_test'}
source = (Path(__file__).resolve().parents[1] / 'install.sh').read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
exec(compile(source, 'install.sh', 'exec'), namespace)


class SystemdInstallerTests(unittest.TestCase):
    def test_install_retry_repair_and_rollback(self):
        self.assertEqual(os.geteuid(), 0, 'Run this test with sudo on a systemd host')
        root = Path(tempfile.mkdtemp(prefix='komari-systemd-install-test-'))
        service_name = root.name
        service = service_name + '.service'
        unit = Path('/etc/systemd/system') / service
        directory = root / 'agent space%literal$dollar'
        good_binary = b'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "komari-agent version 1.2.62"; exit 0; fi\nexec /bin/sleep infinity\n'
        bad_binary = good_binary.replace(b'exec /bin/sleep infinity', b'exit 1')
        payload = good_binary
        corrupt = False

        def download(request, **kwargs):
            if request.full_url.endswith('SHA256SUMS'):
                arch = {'x86_64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}[namespace['platform'].machine()]
                return io.BytesIO((hashlib.sha256(payload).hexdigest() + '  komari-agent-linux-' + arch + '\n').encode())
            return io.BytesIO(b'corrupt' if corrupt else payload)

        common = ['install.sh', '--install-version', '1.2.62', '--install-dir', str(directory),
                  '--install-service-name', service_name]
        credentials = ['-e', 'https://monitor.example.com', '-t', 'isolated-test-token', '--disable-auto-update=false']

        def install(args):
            with patch.object(sys, 'argv', common + args), patch.object(namespace['urllib'].request, 'urlopen', side_effect=download):
                namespace['main']()

        def active():
            subprocess.run(['systemctl', 'is-active', '--quiet', service], check=True)

        try:
            install(credentials)
            active()
            configuration = (directory / 'config.json').read_bytes()
            self.assertIn(b'"disable_auto_update": false', configuration)
            fixed_unit = unit.read_bytes()
            install(credentials)
            active()
            self.assertEqual((directory / 'config.json').read_bytes(), configuration)

            # The second node command must replace credentials, booleans and
            # extra arguments, then a later command must reset omitted options.
            changed = ['-e', 'https://new.example.com', '-t', 'different-test-token',
                       '--disable-web-ssh', '--ignore-unsafe-cert', '--interval', '9']
            install(changed)
            active()
            self.assertEqual(json.loads((directory / 'config.json').read_text()), {
                'endpoint': 'https://new.example.com', 'token': 'different-test-token',
                'disable_auto_update': True, 'disable_web_ssh': True, 'ignore_unsafe_cert': True})
            self.assertIn('"--interval" "9"', unit.read_text())
            self.assertEqual((directory / 'config.json').stat().st_mode & 0o777, 0o600)
            install(credentials)
            active()
            self.assertEqual((directory / 'config.json').read_bytes(), configuration)
            self.assertEqual(unit.read_bytes(), fixed_unit)
            corrupt = True
            with self.assertRaises(ValueError):
                install([])
            active()
            corrupt = False

            payload = bad_binary
            with self.assertRaises(subprocess.CalledProcessError):
                install(changed)
            active()
            self.assertEqual((directory / 'agent').read_bytes(), good_binary)
            self.assertEqual(unit.read_bytes(), fixed_unit)
            self.assertEqual((directory / 'config.json').read_bytes(), configuration)
            self.assertEqual(list(directory.glob('backup-*')), [])
            self.assertEqual(list(directory.glob('*.staged-*')), [])
            payload = good_binary

            # Reproduce the exact 1.2.61 invalid directive, then repair without
            # credentials even though systemd cannot provide a valid ExecStart.
            subprocess.run(['systemctl', 'stop', service], check=True)
            command = ' '.join(namespace['unit_arg'](str(x)) for x in [directory / 'agent', '--config', directory / 'config.json'])
            unit.write_text(namespace['service_unit'](command, directory, legacy=True))
            subprocess.run(['systemctl', 'daemon-reload'], check=True)
            state = subprocess.check_output(['systemctl', 'show', service, '-p', 'LoadState', '--value'], text=True).strip()
            self.assertEqual(state, 'bad-setting')
            install([])
            active()
            self.assertEqual(unit.read_bytes(), fixed_unit)
            self.assertEqual((directory / 'config.json').read_bytes(), configuration)
        finally:
            subprocess.run(['systemctl', 'stop', service], check=False)
            print('Isolated systemd test files and unit retained:', root, unit)


if __name__ == '__main__':
    unittest.main()
