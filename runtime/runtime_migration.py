"""Stage managed runtime files and pin current images without starting a site."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from runtime_images import preserve, selected, write_atomic

IMAGE_KEYS={'nginx':'NGINX_IMAGE_REF','db':'MARIADB_IMAGE_REF','mailpit':'MAILPIT_IMAGE_REF',
            'adminer':'ADMINER_IMAGE_REF','redis':'REDIS_IMAGE_REF'}


def environment(text):
    return dict(line.split('=',1) for line in text.splitlines() if '=' in line and not line.startswith('#'))


def replace_env(text,key,value):
    lines=text.splitlines(); found=False
    for i,line in enumerate(lines):
        if line.startswith(key+'='): lines[i]=key+'='+value;found=True
    if not found: lines.append(key+'='+value)
    return '\n'.join(lines)+'\n'


def stage_site(site:Path,template:Path,manifest:dict)->dict:
    marker=site/'.spawnwp/trixie-prepared.json'
    installed={relative:hashlib.sha256((template/relative).read_bytes()).hexdigest() for relative in manifest['managed']}
    revision=hashlib.sha256(json.dumps({'manifest':manifest,'installed':installed},sort_keys=True).encode()).hexdigest()
    previous_state=json.loads(marker.read_text()) if marker.exists() else {}
    if previous_state.get('revision')==revision:
        return {'status':'unchanged'}
    changes=[];conflicts=[]
    for relative,accepted in manifest['managed'].items():
        source=template/relative; target=site/relative
        if not source.is_file(): raise RuntimeError('Release missing managed file: '+relative)
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if target.is_symlink():
            conflicts.append(relative);continue
        if target.exists():
            if not target.is_file(): conflicts.append(relative);continue
            existing=hashlib.sha256(target.read_bytes()).hexdigest()
            if existing==digest:continue
            if existing not in [*accepted,previous_state.get('installed_hashes',{}).get(relative)]: conflicts.append(relative);continue
        changes.append(relative)
    if conflicts:
        result={'status':'migration_required','conflicts':conflicts}
        write_atomic(site/'.spawnwp/runtime-migration-required.json',json.dumps(result)+'\n')
        return result
    original=(site/'.env').read_text();env=environment(original);updated=original
    containers=subprocess.run(['docker','compose','ps','-aq'],cwd=site,capture_output=True,text=True,check=True).stdout.split()
    infos=json.loads(subprocess.check_output(['docker','inspect',*containers],text=True)) if containers else []
    images={i['Config']['Labels'].get('com.docker.compose.service'):i['Image'] for i in infos}
    if not env.get('PHP_IMAGE_REF'):
        old=images.get('php') or selected(env)
        pinned=preserve(old,env.get('PHP_VERSION','8.3'),env.get('WP_VERSION','latest'))
        updated=replace_env(updated,'PHP_IMAGE_REF',pinned)
    for service,key in IMAGE_KEYS.items():
        if not env.get(key) and service in images:
            ref='spawnwp-retained:'+service+'-'+images[service].removeprefix('sha256:')[:12]
            subprocess.run(['docker','tag',images[service],ref],check=True)
            updated=replace_env(updated,key,ref)
    backup=site/'.spawnwp'/('runtime-files-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    backup.mkdir(parents=True,exist_ok=False,mode=0o700)
    shutil.copy2(site/'.env',backup/'.env')
    previous=[];created=[]
    try:
        for relative in changes:
            target=site/relative
            if target.exists():
                dest=backup/relative;dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(target,dest);previous.append(relative)
            else:created.append(relative)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(template/relative,target)
        debug=site/'docker/php/zz-xdebug.ini'
        if not debug.exists():
            old=site/'docker/php/xdebug.ini'
            shutil.copy2(old if old.exists() else template/'docker/php/zz-xdebug.ini',debug)
            created.append('docker/php/zz-xdebug.ini')
        write_atomic(site/'.env',updated)
        write_atomic(marker,json.dumps({'revision':revision,'backup':str(backup),
                                       'created':created,'replaced':previous,'installed_hashes':installed,'created_at':time.time()})+'\n')
        (site/'.spawnwp/runtime-migration-required.json').unlink(missing_ok=True)
        return {'status':'prepared','backup':str(backup)}
    except Exception:
        write_atomic(site/'.env',original)
        for relative in previous: shutil.copy2(backup/relative,site/relative)
        for relative in created:(site/relative).unlink(missing_ok=True)
        raise


def restore_primary_overrides(template: Path, backups: Path) -> None:
    """The old updater overwrites mutable files before it runs this migration.

    Recover them from its own transaction snapshot once, before installing the
    new updater's preserve-on-update behavior.
    """
    if (template / ".spawnwp" / "trixie-prepared.json").exists() or not backups.is_dir():
        return
    candidates = [p for p in backups.iterdir() if p.is_dir() and (p / "absent.json").is_file()]
    if not candidates:
        return
    backup = max(candidates, key=lambda p: p.stat().st_mtime)
    for name in ("zz-site.ini", "xdebug.ini", "zz-xdebug.ini"):
        source = backup / "runtime" / "docker" / "php" / name
        if source.is_file():
            write_atomic(template / "docker" / "php" / name, source.read_text())
    old_debug = backup / "runtime" / "docker" / "php" / "xdebug.ini"
    new_debug = backup / "runtime" / "docker" / "php" / "zz-xdebug.ini"
    if old_debug.is_file() and not new_debug.exists():
        write_atomic(template / "docker" / "php" / "zz-xdebug.ini", old_debug.read_text())
