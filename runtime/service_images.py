"""Prepare and apply vendor images, verifying the image actually serving the site."""
from __future__ import annotations
import fcntl
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from php_security import status
from runtime_images import docker, write_atomic
from runtime_migration import replace_env

ADMINER_REFRESH_TAG = 'adminer:6'
ADMINER_BASELINE = 'adminer:6.3.0@sha256:3a07decd833c2375ea749aa8018250c1db4859a78bd55354f797bfb591733233'
MIN_ADMINER = (6, 3, 0)


def state_root():
    return Path(os.environ.get('SPAWNWP_IMAGE_STATE_ROOT', '/var/lib/spawnwp/images'))


def compose(project, *args):
    return subprocess.check_output(['docker', 'compose', *args], cwd=project, text=True).strip()


def config(project):
    return json.loads(compose(project, 'config', '--format', 'json'))


def php_version(reference):
    return docker('run', '--rm', '--network', 'none', '--read-only', '--memory', '128m',
                  '--cpus', '0.5', '--pids-limit', '16', '--user', '65534:65534',
                  '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                  '--entrypoint', 'php', reference, '-r', 'echo PHP_VERSION;')


def image_details(reference):
    info = json.loads(docker('image', 'inspect', reference))[0]
    env = dict(item.split('=', 1) for item in (info.get('Config', {}).get('Env') or []) if '=' in item)
    return {'image_id': info['Id'], 'adminer_version': env.get('ADMINER_VERSION', 'unknown')}


def verify_adminer(reference):
    report = image_details(reference)
    version = report['adminer_version']
    if not re.fullmatch(r'6\.\d+\.\d+', version) or tuple(map(int, version.split('.'))) < MIN_ADMINER:
        raise RuntimeError('Prepare an official Adminer 6 update (6.3.0 or newer) first')
    report['php_full_version'] = php_version(reference)
    if status(report['php_full_version'])['status'] != 'patched':
        raise RuntimeError('Adminer PHP patch status is not verified')
    return report


def prepared_adminer():
    path = state_root() / 'adminer.json'
    if not path.exists():
        return {'status': 'not_prepared'}
    try:
        record = json.loads(path.read_text())
        version = record.get('adminer_version', '')
        if not re.fullmatch(r'6\.\d+\.\d+', version) or tuple(map(int, version.split('.'))) < MIN_ADMINER:
            return {'status': 'prepare_required'}
        if not re.fullmatch(r'adminer@sha256:[a-f0-9]{64}', record.get('reference', '')):
            raise ValueError('Invalid Adminer reference')
        return {**record, 'status': 'prepared'}
    except (ValueError, TypeError, AttributeError, OSError):
        return {'status': 'prepare_required'}


def prepare_adminer():
    descriptor = json.loads(docker('buildx', 'imagetools', 'inspect', ADMINER_REFRESH_TAG,
                                   '--format', '{{json .Manifest}}'))
    digest = descriptor.get('digest') or descriptor.get('Digest')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest or ''):
        raise RuntimeError('Could not resolve the Adminer image identity')
    reference = 'adminer@' + digest
    docker('pull', reference)
    report = {'reference': reference, **verify_adminer(reference)}
    write_atomic(state_root() / 'adminer.json', json.dumps(report) + '\n')
    return {**report, 'status': 'prepared'}


def new_adminer():
    record = prepared_adminer()
    reference = record['reference'] if record['status'] == 'prepared' else ADMINER_BASELINE
    docker('pull', reference)
    verify_adminer(reference)
    return reference


def candidate(project, service):
    if service == 'nginx':
        return json.loads((project / 'docker/php/runtime-lock.json').read_text())['images']['nginx']
    record = prepared_adminer()
    if record['status'] != 'prepared':
        raise RuntimeError('Use System → Prepare Adminer update before applying it to a site')
    return record['reference']


def managed_adminer_compose(text):
    """Convert only a recognized image declaration; preserve all other bytes."""
    lines = text.splitlines(keepends=True)
    inside = False
    found = 0
    for index, line in enumerate(lines):
        if re.fullmatch(r'  adminer:\s*\n?', line):
            inside = True
            continue
        if inside and re.match(r'^\S|^  \S', line):
            inside = False
        if inside and re.match(r'^    image:', line):
            found += 1
            value = line.split(':', 1)[1].strip().strip('\"\'')
            if value.startswith('${ADMINER_IMAGE_REF:-') and value.endswith('}'):
                continue
            if not re.fullmatch(r'adminer:(?:5(?:\.\d+){0,2}|6(?:\.\d+){0,2})(?:@sha256:[a-f0-9]{64})?', value):
                raise RuntimeError('Custom Adminer image: review its Compose configuration before updating')
            lines[index] = '    image: ${ADMINER_IMAGE_REF:-' + value + '}\n'
    if found != 1:
        raise RuntimeError('Unsupported Adminer Compose declaration: manual migration review required')
    return ''.join(lines)


def running_adminer(project):
    """Report metadata of the actual container image, never infer from a tag."""
    try:
        ids = compose(project, 'ps', '-aq', 'adminer').split()
        if not ids:
            return {'adminer_version': 'unknown', 'image_id': '', 'state': 'absent'}
        info = json.loads(docker('inspect', ids[0]))[0]
        report = image_details(info['Image'])
        return {**report, 'reference': info['Config']['Image'],
                'state': 'running' if info['State']['Running'] else 'stopped'}
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, IndexError):
        return {'adminer_version': 'unknown', 'image_id': '', 'state': 'unknown'}


def update(project: Path, service: str) -> dict:
    if service not in {'nginx', 'adminer'}:
        raise ValueError('Unsupported service update')
    lock_root = Path(os.environ.get('SPAWNWP_IMAGE_LOCK_ROOT', '/run/lock'))
    lock_root.mkdir(parents=True, exist_ok=True)
    with (lock_root / f'spawnwp-php-switch-{project.name}.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = candidate(project, service)
        docker('pull', target)
        report = {'php_full_version': ''}
        if service == 'adminer':
            report = verify_adminer(target)
        else:
            result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                                     '--cap-drop', 'ALL', '--user', '65534:65534', '--entrypoint',
                                     'nginx', target, '-v'], capture_output=True, text=True, check=True)
            if not re.search(r'nginx/1\.30\.6\b', result.stderr + result.stdout):
                raise RuntimeError('Unexpected Nginx candidate version')
        expected = docker('image', 'inspect', '-f', '{{.Id}}', target)
        path = project / '.env'
        compose_path = project / 'compose.yaml'
        original, original_compose = path.read_text(), compose_path.read_text()
        before = config(project)
        ids = compose(project, 'ps', '-aq', service).split()
        current = json.loads(docker('inspect', ids[0]))[0] if ids else None
        running = bool(current and current['State']['Running'])
        identity = current['Image'] if current else docker('image', 'inspect', '-f', '{{.Id}}', before['services'][service]['image'])
        previous = 'spawnwp-retained:' + service + '-' + identity.removeprefix('sha256:')[:12]
        docker('tag', identity, previous)
        key = 'NGINX_IMAGE_REF' if service == 'nginx' else 'ADMINER_IMAGE_REF'
        record = {'environment': replace_env(original, key, previous), 'original_environment': original,
                  'compose': original_compose, 'previous_ref': previous,
                  'created_at': time.time(), 'retain_until': time.time() + 14 * 86400}
        rollback_path = project / '.spawnwp' / f'{service}-rollback.json'
        if identity != expected or not rollback_path.exists():
            write_atomic(rollback_path, json.dumps(record) + '\n')
        recreated = False
        try:
            if service == 'adminer':
                write_atomic(compose_path, managed_adminer_compose(original_compose))
            write_atomic(path, replace_env(original, key, target))
            after = config(project)
            if after['services'][service]['image'] != target:
                raise RuntimeError('Compose did not select the prepared image. Review image/environment overrides')
            normalized = json.loads(json.dumps(after))
            normalized['services'][service]['image'] = before['services'][service]['image']
            if normalized != before:
                raise RuntimeError('Compose changes exceed the selected service image; manual review required')
            response = {'project': project.name, 'reference': target, **report}
            if not running:
                return {**response, 'status': 'selected'}
            if current['Image'] == expected:
                ready(project, service, expected)
                return {**response, 'status': 'unchanged'}
            recreated = True
            compose(project, 'up', '-d', '--no-deps', '--no-build', '--force-recreate', service)
            ready(project, service, expected)
            return {**response, 'status': 'patched' if service == 'adminer' else 'updated'}
        except Exception as error:
            write_atomic(path, original)
            write_atomic(compose_path, original_compose)
            if recreated:
                try:
                    # Pin the rollback even when the old Compose used a mutable tag.
                    if service == 'adminer':
                        write_atomic(compose_path, managed_adminer_compose(original_compose))
                    write_atomic(path, record['environment'])
                    compose(project, 'up', '-d', '--no-deps', '--no-build', '--force-recreate', service)
                    ready(project, service, identity)
                except Exception as rollback_error:
                    raise RuntimeError(f'Update failed ({error}); rollback also failed ({rollback_error}). Review the saved rollback record') from rollback_error
            raise


def ready(project, service, expected=None):
    cid = compose(project, 'ps', '-q', service)
    for _ in range(60):
        info = json.loads(docker('inspect', cid))[0]
        if expected and info['Image'] != expected:
            raise RuntimeError('Running service image does not match the selected image')
        health = info['State'].get('Health', {}).get('Status')
        if health == 'healthy':
            break
        if info['State']['Status'] in {'exited', 'dead'} or health == 'unhealthy':
            raise RuntimeError(service + ' did not become healthy')
        time.sleep(2)
    else:
        raise RuntimeError(service + ' readiness timed out')
    if service == 'nginx':
        compose(project, 'exec', '-T', 'nginx', 'nginx', '-t')
    else:
        live = compose(project, 'exec', '-T', 'adminer', 'php', '-r', 'echo PHP_VERSION;')
        if status(live)['status'] != 'patched':
            raise RuntimeError('Running Adminer PHP is not verified')
        settings = config(project)['services']['adminer']
        ports = [p for p in settings.get('ports', []) if int(p['target']) == 8080]
        if not ports:
            raise RuntimeError('Adminer HTTP port is not configured')
        port = compose(project, 'port', 'adminer', '8080').splitlines()[0].rsplit(':', 1)[-1]
        if not port.isdigit():
            raise RuntimeError('Invalid Adminer HTTP port')
        # Probe the exact container port, without following redirects to another site.
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(f'http://127.0.0.1:{port}/', timeout=10) as response:
            if response.status != 200 or b'Adminer' not in response.read():
                raise RuntimeError('Adminer HTTP verification failed')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
