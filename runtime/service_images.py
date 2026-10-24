"""Prepare and apply vendor service images independently from PHP/database changes."""
from __future__ import annotations
import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path
from php_security import status
from runtime_images import docker, write_atomic
from runtime_migration import environment, replace_env


def state_root():
    return Path(os.environ.get('SPAWNWP_IMAGE_STATE_ROOT','/var/lib/spawnwp/images'))


def php_version(reference):
    return docker('run','--rm','--network','none','--read-only','--memory','128m','--pids-limit','16',
                  '--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges',
                  '--entrypoint','php',reference,'-r','echo PHP_VERSION;')


def prepare_adminer():
    descriptor=json.loads(docker('buildx','imagetools','inspect','adminer:5','--format','{{json .Manifest}}'))
    digest=descriptor.get('digest') or descriptor.get('Digest')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest or ''):
        raise RuntimeError('Could not resolve the Adminer image identity')
    reference='adminer@'+digest
    docker('pull',reference)
    version=php_version(reference)
    if status(version)['status']!='patched':raise RuntimeError('Adminer PHP patch status is not verified')
    write_atomic(state_root()/'adminer.json',json.dumps({'reference':reference,'php_full_version':version})+'\n')
    return {'reference':reference,'php_full_version':version,'status':'patched'}


def candidate(project,service):
    if service=='nginx':
        return json.loads((project/'docker/php/runtime-lock.json').read_text())['images']['nginx']
    record=state_root()/'adminer.json'
    if record.exists():return json.loads(record.read_text())['reference']
    config=json.loads(subprocess.check_output(['docker','compose','config','--format','json'],cwd=project,text=True))
    # A release-pinned fallback is safe to prepare; never use an unverified local tag.
    ref=config['services']['adminer']['image']
    if '@sha256:' not in ref:raise RuntimeError('Prepare a verified Adminer image first')
    return ref


def update(project:Path,service:str)->dict:
    if service not in {'nginx','adminer'}:raise ValueError('Unsupported service update')
    lock_root=Path(os.environ.get('SPAWNWP_IMAGE_LOCK_ROOT','/run/lock'))
    lock_root.mkdir(parents=True,exist_ok=True)
    # Share the project runtime lock with PHP switching.
    with (lock_root/f'spawnwp-php-switch-{project.name}.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        target=candidate(project,service)
        docker('pull',target)
        version=''
        if service=='adminer':
            version=php_version(target)
            if status(version)['status']!='patched':raise RuntimeError('Candidate Adminer PHP is not patched')
        else:
            result=subprocess.run(['docker','run','--rm','--network','none','--read-only',
                                   '--cap-drop','ALL','--user','65534:65534','--entrypoint','nginx',target,'-v'],
                                  capture_output=True,text=True,check=True)
            if not re.search(r'nginx/1\.30\.6\b',result.stderr+result.stdout):
                raise RuntimeError('Unexpected Nginx candidate version')
        path=project/'.env';original=path.read_text();env=environment(original)
        key='NGINX_IMAGE_REF' if service=='nginx' else 'ADMINER_IMAGE_REF'
        ids=subprocess.check_output(['docker','compose','ps','-aq',service],cwd=project,text=True).split()
        config=json.loads(subprocess.check_output(['docker','compose','config','--format','json'],cwd=project,text=True))
        current=json.loads(docker('inspect',ids[0]))[0] if ids else None
        identity=current['Image'] if current else docker('image','inspect','-f','{{.Id}}',config['services'][service]['image'])
        previous='spawnwp-retained:'+service+'-'+identity.removeprefix('sha256:')[:12]
        docker('tag',identity,previous)
        rollback=replace_env(original,key,previous)
        write_atomic(project/'.spawnwp'/f'{service}-rollback.json',json.dumps({'environment':rollback,'previous_ref':previous,
                       'created_at':time.time(),'retain_until':time.time()+14*86400})+'\n')
        write_atomic(path,replace_env(original,key,target))
        running=current and current['State']['Running']
        if not running:return {'project':project.name,'status':'selected','php_full_version':version}
        try:
            subprocess.run(['docker','compose','up','-d','--no-deps','--no-build','--force-recreate',service],cwd=project,check=True)
            ready(project,service)
            return {'project':project.name,'status':'patched' if service=='adminer' else 'updated','php_full_version':version}
        except Exception:
            write_atomic(path,rollback)
            subprocess.run(['docker','compose','up','-d','--no-deps','--no-build','--force-recreate',service],cwd=project,check=True)
            ready(project,service)
            raise


def ready(project,service):
    cid=subprocess.check_output(['docker','compose','ps','-q',service],cwd=project,text=True).strip()
    for _ in range(60):
        info=json.loads(docker('inspect',cid))[0]
        health=info['State'].get('Health',{}).get('Status')
        if health=='healthy':break
        if info['State']['Status'] in {'exited','dead'} or health=='unhealthy':
            raise RuntimeError(service+' did not become healthy')
        time.sleep(2)
    else:raise RuntimeError(service+' readiness timed out')
    if service=='nginx':
        subprocess.run(['docker','compose','exec','-T','nginx','nginx','-t'],cwd=project,check=True)
