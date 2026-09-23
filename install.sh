#!/usr/bin/env bash
# Standalone Linux/systemd installer. Reinstallation replaces configuration.
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
        raise argparse.ArgumentTypeError('Use an explicit semantic release version, for example 1.2.63.')
    return value

def unit_arg(value):
    if any(c in value for c in '\r\n\0'):
        raise ValueError('Newlines and NUL are not allowed in service arguments.')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'

def unit_directory(value):
    # WorkingDirectory is a path directive, not an ExecStart argument: no quotes
    # or dollar expansion. Reject characters that cannot be represented literally.
    if not value.startswith('/') or value != value.strip() or any(c in value for c in '\r\n\0\\'):
        raise ValueError('Installation directory must be absolute and contain no backslashes, control characters or surrounding whitespace.')
    return value.replace('%', '%%')

def service_unit(command, directory, legacy=False):
    working_directory = unit_arg(str(directory)) if legacy else unit_directory(str(directory))
    return ('[Unit]\nDescription=Komari Agent (mghts)\nAfter=network-online.target\nWants=network-online.target\n\n'
            '[Service]\nType=simple\nExecStart=' + command + '\nWorkingDirectory=' + working_directory +
            '\nRestart=always\nRestartSec=5\nUser=root\n\n[Install]\nWantedBy=multi-user.target\n')

def agent_command(target, config, extra=(), legacy=False):
    executable = unit_arg(str(target))
    # systemd expands dollars in arguments, but never in the executable path.
    if not legacy:
        executable = executable.replace('$$', '$')
    return executable + ' ' + ' '.join(unit_arg(str(x)) for x in ['--config', config, *extra])

def managed_unit(text, directory, target, config):
    commands = [line[len('ExecStart='):] for line in text.splitlines() if line.startswith('ExecStart=')]
    if len(commands) != 1:
        return None
    command = commands[0]
    for legacy in (True, False):
        prefix = agent_command(target, config, legacy=legacy)
        if (command == prefix or command.startswith(prefix + ' ')) and text == service_unit(command, directory, legacy=legacy):
            return service_unit(agent_command(target, config) + command[len(prefix):], directory)
    return None

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

def atomic_write(target, contents, mode):
    staged = target.with_name(target.name + '.staged-' + str(time.time_ns()))
    with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), 'wb') as output:
        output.write(contents)
        os.fchmod(output.fileno(), mode)
    os.replace(staged, target)

def main():
    parser = argparse.ArgumentParser(description='Install or reinstall the mghts Linux Agent; endpoint/token replace existing settings. Omit both to upgrade while preserving settings. No backup directories are created.')
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
    unit_directory(str(directory))
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
    if target.is_symlink() or config.is_symlink() or unit.is_symlink():
        raise ValueError('Symlink installation files are not supported.')
    fragment = run('systemctl', 'show', service, '--property=FragmentPath', '--value').stdout.strip()
    existing = bool(fragment) or unit.exists()
    original_unit = unit.read_text() if unit.exists() else ''
    replacement_unit = managed_unit(original_unit, directory, target, config) if not fragment or fragment == str(unit) else None
    reconfigure = bool(args.endpoint)
    if existing and replacement_unit is None:
        actual = run('systemctl', 'show', service, '--property=ExecStart', '--value').stdout
        if 'path=' + str(target) + ' ' not in actual and 'path=' + str(target) + ';' not in actual:
            raise ValueError('Existing service uses another binary path; supply its --install-dir. No service was changed.')
    if not existing and not args.endpoint:
        raise ValueError('A new installation requires --endpoint and --token.')
    if not reconfigure and existing and (extra or args.disable_auto_update != 'true' or args.disable_web_ssh or args.ignore_unsafe_cert):
        raise ValueError('Upgrade mode preserves existing settings; edit the existing configuration separately.')
    if reconfigure:
        command = agent_command(target, config, extra)
        replacement_unit = service_unit(command, directory)
        configuration = {'endpoint': args.endpoint, 'token': args.token,
                         'disable_auto_update': args.disable_auto_update == 'true',
                         'disable_web_ssh': args.disable_web_ssh,
                         'ignore_unsafe_cert': args.ignore_unsafe_cert}
    directory.mkdir(parents=True, exist_ok=True)
    # Keep recovery data only in this process, never in a backup directory.
    originals = {}
    for source in (target, config, unit):
        if source.exists():
            originals[source] = (source.read_bytes(), source.stat().st_mode & 0o777)
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', service]).returncode == 0
    step = 'write installation files'
    try:
        if replacement_unit is not None and replacement_unit != original_unit:
            atomic_write(unit, replacement_unit.encode(), 0o644)
            step = 'reload repaired service definition'
            run('systemctl', 'daemon-reload')
        if existing:
            step = 'stop existing service'
            run('systemctl', 'stop', service)
        if reconfigure:
            step = 'replace configuration'
            atomic_write(config, (json.dumps(configuration, indent=2) + '\n').encode(), 0o600)
        step = 'replace binary'
        atomic_copy(staged/asset, target)
        step = 'reload systemd configuration'
        run('systemctl', 'daemon-reload')
        step = 'start service'
        run('systemctl', 'start', service)
        time.sleep(3)
        step = 'check service is active'
        run('systemctl', 'is-active', '--quiet', service)
        # Explicit installations are enabled only after successful startup.
        if reconfigure:
            step = 'enable service'
            run('systemctl', 'enable', service)
    except Exception:
        subprocess.run(['systemctl', 'stop', service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for path, (contents, mode) in originals.items():
                atomic_write(path, contents, mode)
            run('systemctl', 'daemon-reload')
            if was_active:
                run('systemctl', 'start', service)
        except Exception:
            print('Automatic recovery failed; inspect the service state before retrying.', file=sys.stderr)
        print('Installation failed during: ' + step + '. Existing files were restored when possible; no backup directory was created.', file=sys.stderr)
        print('Retry the node installation command. Files from a first installation are retained for retry.', file=sys.stderr)
        print('Inspect locally: systemctl show ' + service + ' -p LoadState -p ActiveState -p SubState -p Result -p LoadError', file=sys.stderr)
        raise
    print('Service started. Confirm the node is online in your panel.')
    print('Downloads retained at: ' + str(staged))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # Do not print argv or command output, which can contain a node token.
        print('Install failed: ' + (str(error) if not isinstance(error, subprocess.CalledProcessError) else 'service or binary validation failed'), file=sys.stderr)
        sys.exit(1)
PY
