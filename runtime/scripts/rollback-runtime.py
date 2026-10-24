#!/usr/bin/env python3
"""Restore the last selected PHP image, preserving newer site settings and data."""
import argparse
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from php_security import image_report
from runtime_images import write_atomic
from runtime_migration import environment, replace_env


def rollback(project:Path,accept_legacy=False,accepted_image_id=''):
    spec=importlib.util.spec_from_file_location('php_switch',Path(__file__).with_name('php-switch-progress.py'))
    switch=importlib.util.module_from_spec(spec);spec.loader.exec_module(switch)
    lock_root=Path(os.environ.get('SPAWNWP_IMAGE_LOCK_ROOT','/run/lock'));lock_root.mkdir(parents=True,exist_ok=True)
    with (lock_root/f'spawnwp-php-switch-{project.name}.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        record=json.loads((project/'.spawnwp/runtime-rollback.json').read_text())
        prior=environment(record['environment']);reference=record['previous_ref']
        report=image_report(reference)
        if not report.get('image_id'):raise RuntimeError('The retained image is not available')
        if (report.get('legacy') or report.get('status')!='patched') and not (accept_legacy and accepted_image_id==report['image_id']):
            raise RuntimeError('Review and accept the exact EOL image before rolling back')
        original=(project/'.env').read_text();updated=original
        for key in ['PHP_VERSION','WP_IMAGE_SUFFIX']:
            updated=replace_env(updated,key,prior.get(key,''))
        updated=replace_env(updated,'PHP_IMAGE_REF',reference)
        running=subprocess.check_output(['docker','compose','ps','--services','--filter','status=running'],cwd=project,text=True)
        command=['docker','compose','up','-d','--no-deps','--no-build','--force-recreate','php']
        write_atomic(project/'.env',updated)
        try:
            if 'php' in running.split():
                subprocess.run(command,cwd=project,check=True)
                if not switch.healthy(project) or not switch.site_ready(project):
                    raise RuntimeError('The retained runtime failed readiness verification')
        except Exception:
            write_atomic(project/'.env',original)
            subprocess.run(command,cwd=project,check=True)
            if not switch.healthy(project) or not switch.site_ready(project):
                raise RuntimeError('Both rollback and recovery failed; backups and images are retained')
            raise
        print('Selected retained runtime: '+reference)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',type=Path,default=Path.cwd())
    parser.add_argument('--accept-legacy',action='store_true')
    parser.add_argument('--accepted-image-id',default='')
    args=parser.parse_args()
    rollback(args.project.resolve(),args.accept_legacy,args.accepted_image_id)
