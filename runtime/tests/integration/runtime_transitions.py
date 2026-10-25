"""Exercise real Docker apply/rollback operations using explicitly prepared images.

Only cache lookup and the retained-tag namespace are injected. Container recreation,
security checks, HTTP/database readiness and rollback run the production code.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import uuid
from pathlib import Path
from unittest import mock


def exercise(project: Path, images: dict[str,str], initial: str) -> dict:
    import runtime_images
    path=project/'scripts/php-switch-progress.py'
    spec=importlib.util.spec_from_file_location('runtime_switch_integration',path)
    switch=importlib.util.module_from_spec(spec);spec.loader.exec_module(switch)
    prefix='spawnwp-transition-'+uuid.uuid4().hex[:10]
    references=[]
    identities={series:subprocess.check_output(['docker','image','inspect','-f','{{.Id}}',ref],text=True).strip()
                for series,ref in images.items()}
    expected={series:json.loads(subprocess.check_output(['docker','image','inspect',ref],text=True))[0]
              for series,ref in images.items()}
    def verify_image(series):
        cid=subprocess.check_output(['docker','compose','ps','-q','php'],cwd=project,text=True).strip()
        image=subprocess.check_output(['docker','inspect','-f','{{.Image}}',cid],text=True).strip()
        actual=json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]
        if any(actual[key]!=expected[series][key] for key in ['RootFS','Config','Architecture']):
            raise RuntimeError('The running image does not match the exact selected runtime content/configuration')
    def cli(*args):
        return subprocess.check_output(['docker','compose','exec','-T','-u','www-data','php',*args],cwd=project,text=True).strip()
    def identity(_project,series,wp):
        return images[series],runtime_images.suffix(wp),'prepared-integration-image'
    def prepare(_project,series,*args,**kwargs):return images[series]
    token=uuid.uuid4().hex
    cli('wp','option','update','spawnwp_transition_token',token)
    core=cli('wp','core','version')
    (project/'.spawnwp/runtime-migrated.json').write_text('{}')
    env=project/'.env'
    original_ready=switch.site_ready
    def apply(target,fail=False):
        output=io.StringIO()
        count=0
        def readiness(site):
            nonlocal count
            count+=1
            return False if fail and count==1 else original_ready(site)
        with mock.patch.object(switch,'site_ready',side_effect=readiness),contextlib.redirect_stdout(output):
            result=switch.switch(project,target,project/'.spawnwp/locks',
                                 accept_vulnerable=target=='7.4',accepted_image_id=identities[target])
        if result!=(1 if fail else 0):raise RuntimeError('Runtime apply failed: '+output.getvalue()[-2000:])
        for name in ('runtime-rollback.json','runtime-history.json'):
            records=json.loads((project/'.spawnwp'/name).read_text())
            if isinstance(records,dict):records=[records]
            references.extend(r['previous_ref'] for r in records if r.get('previous_ref','').startswith(prefix+':'))
    # An Eulerian circuit covers every ordered pair exactly once.
    edges={node:[other for other in sorted(images) if other!=node] for node in images}
    stack=[initial];circuit=[]
    while stack:
        if edges[stack[-1]]:stack.append(edges[stack[-1]].pop())
        else:circuit.append(stack.pop())
    sequence=list(reversed(circuit));transitions=[]
    try:
        with mock.patch.object(switch,'prepare',side_effect=prepare),\
             mock.patch.object(switch,'image_identity',side_effect=identity),\
             mock.patch.object(runtime_images,'PHP_IMAGE_REPO',prefix),\
             mock.patch.dict(os.environ,{'SPAWNWP_METRICS_FILE':str(project/'.spawnwp/test-metrics.json')}):
            for previous,target in zip(sequence,sequence[1:]):
                apply(target)
                verify_image(target)
                if not cli('php','-r','echo PHP_VERSION;').startswith(target+'.'):
                    raise RuntimeError('The selected PHP binary is not serving the requested branch')
                if cli('wp','option','get','spawnwp_transition_token')!=token or cli('wp','core','version')!=core:
                    raise RuntimeError('Runtime switch changed WordPress core or database content')
                transitions.append({'previous':previous,'target':target,'result':'passed'})
                print('PASS actual PHP transition '+previous+' -> '+target,flush=True)
            target=next(series for series in images if series!=initial)
            apply(target,fail=True)
            verify_image(initial)
            if not cli('php','-r','echo PHP_VERSION;').startswith(initial+'.'):
                raise RuntimeError('HTTP failure did not restore the exact previous runtime branch')
            subprocess.run(['docker','compose','stop','nginx','php'],cwd=project,check=True,capture_output=True)
            apply(target)
            running=subprocess.check_output(['docker','compose','ps','--services','--filter','status=running'],cwd=project,text=True)
            if 'php' in running.split() or 'nginx' in running.split():
                raise RuntimeError('A Down site was started by the PHP selector')
            return {'pairs':transitions,'http_failure_rollback':'passed','down_site_selection':'passed',
                    'retained_test_refs':sorted(set(references))}
    except Exception:
        for ref in references:
            subprocess.run(['docker','image','rm',ref],capture_output=True)
        raise
