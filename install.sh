#!/usr/bin/env bash
# Standalone Linux/systemd installer. Downloads and backups are retained.
set -euo pipefail
command -v python3 >/dev/null || { echo 'python3 is required.' >&2; exit 1; }
exec python3 - "$@" <<'PY'
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

REPOSITORY = 'mghts/komari-agent'

def version(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?', value):
        raise argparse.ArgumentTypeError('Use an explicit semantic release version, for example 1.2.61.')
    return value

def unit_arg(value):
    if any(c in value for c in '\r\n\0'):
        raise ValueError('Newlines and NUL are not allowed in service arguments.')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'

def verify(binary, checksum_file, asset):
    entries = [line.split() for line in checksum_file.read_text().splitlines()]
    matches = [parts[0] for parts in entries if len(parts) == 2 and parts[1].lstrip('*') == asset]
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if len(matches) != 1 or matches[0] != digest:
        raise ValueError('SHA256 verification failed; the running service has not been touched.')

def run(*args):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def atomic_copy(source, target):
    staged = target.with_name(target.name + '.staged-' + str(time.time_ns()))
    shutil.copy2(source, staged)
    os.replace(staged, target)

def main():
    parser = argparse.ArgumentParser(description='Install or upgrade the mghts Linux Agent with verified downloads and retained backups.')
    parser.add_argument('--install-version', required=True, type=version)
    parser.add_argument('--install-dir', default='/opt/komari')
    parser.add_argument('--install-service-name', default='komari-agent')
    parser.add_argument('--install-ghproxy', default='')
    parser.add_argument('-e', '--endpoint')
    parser.add_argument('-t', '--token')
    parser.add_argument('--disable-auto-update', nargs='?', const='true', choices=['true', 'false'], default='true')
    parser.add_argument('--disable-web-ssh', action='store_true')
    parser.add_argument('--ignore-unsafe-cert', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args, extra = parser.parse_known_args()
    if platform.system() != 'Linux':
        parser.error('This fork installer supports Linux/systemd only.')
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
    if not arch:
        parser.error('Supported architectures: x86_64 and ARM64.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.install_service_name):
        parser.error('Invalid service name.')
    directory = Path(args.install_dir)
    if not directory.is_absolute() or directory.is_symlink():
        parser.error('Installation directory must be an absolute, non-symlink path.')
    if bool(args.endpoint) != bool(args.token):
        parser.error('Supply endpoint and token together, or omit both to preserve an existing service.')
    if args.install_ghproxy and not args.install_ghproxy.startswith('https://'):
        parser.error('The download proxy must use HTTPS.')
    asset = 'komari-agent-linux-' + arch
    base = 'https://github.com/' + REPOSITORY + '/releases/download/' + args.install_version + '/'
    if args.install_ghproxy:
        base = args.install_ghproxy.rstrip('/') + '/' + base
    staged = Path(tempfile.mkdtemp(prefix='komari-agent-download-'))
    os.chmod(staged, 0o700)
    print('Downloading release ' + args.install_version + ' (' + arch + ')', flush=True)
    for name in [asset, 'SHA256SUMS']:
        request = urllib.request.Request(base + name, headers={'User-Agent': 'mghts-komari-installer'})
        with urllib.request.urlopen(request, timeout=60) as response, (staged/name).open('wb') as output:
            shutil.copyfileobj(response, output)
    verify(staged/asset, staged/'SHA256SUMS', asset)
    os.chmod(staged/asset, 0o755)
    # Only the verified new executable is queried; old agents may not support --version.
    reported = run(str(staged/asset), '--version').stdout.strip()
    if reported != 'komari-agent version ' + args.install_version:
        raise ValueError('Downloaded binary version does not match the selected release.')
    print('Verified: ' + reported)
    if args.dry_run:
        print('Dry run complete. No service or installation files were changed. Download: ' + str(staged))
        return
    if os.geteuid() != 0 or not shutil.which('systemctl'):
        raise ValueError('Root and a working systemd installation are required.')
    service = args.install_service_name + '.service'
    target = directory/'agent'
    config = directory/'config.json'
    unit = Path('/etc/systemd/system')/service
    fragment = run('systemctl', 'show', service, '--property=FragmentPath', '--value').stdout.strip()
    existing = bool(fragment)
    if existing and not args.endpoint:
        actual = run('systemctl', 'show', service, '--property=ExecStart', '--value').stdout
        if 'path=' + str(target) + ' ' not in actual and 'path=' + str(target) + ';' not in actual:
            raise ValueError('Existing service uses another binary path; supply its --install-dir. No service was changed.')
    if not existing and not args.endpoint:
        raise ValueError('A new installation requires --endpoint and --token.')
    if args.endpoint and (config.exists() or existing):
        raise ValueError('An installation already exists. Omit endpoint/token to preserve its configuration during upgrade.')
    directory.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or config.is_symlink() or unit.is_symlink():
        raise ValueError('Symlink installation files are not supported.')
    backup = directory/('backup-' + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    for source, name in [(target, 'agent'), (config, 'config.json'), (unit, 'service.unit')]:
        if source.exists():
            shutil.copy2(source, backup/name)
    if args.endpoint:
        configuration = {'endpoint': args.endpoint, 'token': args.token,
                         'disable_auto_update': args.disable_auto_update == 'true',
                         'disable_web_ssh': args.disable_web_ssh,
                         'ignore_unsafe_cert': args.ignore_unsafe_cert}
        config.write_text(json.dumps(configuration, indent=2) + '\n')
        os.chmod(config, 0o600)
        command = ' '.join(unit_arg(x) for x in [str(target), '--config', str(config), *extra])
        unit.write_text('[Unit]\nDescription=Komari Agent (mghts)\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nExecStart=' + command + '\nWorkingDirectory=' + unit_arg(str(directory)) + '\nRestart=always\nRestartSec=5\nUser=root\n\n[Install]\nWantedBy=multi-user.target\n')
    elif extra or args.disable_auto_update != 'true' or args.disable_web_ssh or args.ignore_unsafe_cert:
        raise ValueError('Upgrade mode preserves existing settings; edit the existing configuration separately.')
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', service]).returncode == 0
    try:
        if existing:
            run('systemctl', 'stop', service)
        atomic_copy(staged/asset, target)
        run('systemctl', 'daemon-reload')
        if not existing:
            run('systemctl', 'enable', service)
        run('systemctl', 'start', service)
        time.sleep(3)
        run('systemctl', 'is-active', '--quiet', service)
    except Exception:
        subprocess.run(['systemctl', 'stop', service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if (backup/'agent').exists():
            atomic_copy(backup/'agent', target)
            if was_active:
                run('systemctl', 'start', service)
        print('Installation failed. Previous binary restored when available. Backup: ' + str(backup), file=sys.stderr)
        raise
    print('Service started. Confirm the node is online in your panel. Backup: ' + str(backup))
    print('Downloads retained at: ' + str(staged))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # Do not print argv or command output, which can contain a node token.
        print('Install failed: ' + (str(error) if not isinstance(error, subprocess.CalledProcessError) else 'service or binary validation failed'), file=sys.stderr)
        sys.exit(1)
PY
