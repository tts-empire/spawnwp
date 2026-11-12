#!/usr/bin/env python3
"""Exercise real vendor images in an isolated Compose project, including legacy files."""
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RUNTIME))
import service_images as images

OLD = 'adminer:5@sha256:87f9aeeef91638f5bc150bb6490a4fd85d071f458f38dcc8171a640e1751d29b'


def main():
    root = Path(tempfile.mkdtemp(prefix='spawnwp-adminer-test-'))
    os.environ['SPAWNWP_IMAGE_STATE_ROOT'] = str(root / 'image-state')
    os.environ['SPAWNWP_IMAGE_LOCK_ROOT'] = str(root / 'locks')
    results = []
    def check(name, value=True):
        if not value:raise AssertionError(name)
        results.append(name);print('PASS ' + name, flush=True)
    def call(*args):return images.compose(root, *args)
    def info(service):return json.loads(images.docker('inspect', call('ps', '-aq', service)))[0]
    def login():
        port=call('port','adminer','8080').rsplit(':',1)[-1]
        url='http://127.0.0.1:'+port+'/'
        browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        html=browser.open(url,timeout=15).read().decode()
        match=re.search(r'''name=['"]token['"]\s+value=['"]([^'"]+)''',html)
        check('login page exposes bridge-compatible CSRF token',bool(match))
        fields={'auth[driver]':'server','auth[server]':'db','auth[username]':'wpuser',
                'auth[password]':'smoke-only','auth[db]':'wordpress','auth[permanent]':'1','token':'invalid'}
        try:
            bad=browser.open(url,urllib.parse.urlencode(fields).encode(),timeout=15)
            check('invalid login token rejected',b'Logout' not in bad.read())
        except urllib.error.HTTPError as error:
            check('invalid login token rejected',error.code in (400,403))
        html=browser.open(url,timeout=15).read().decode()
        fields['token']=re.search(r'''name=['"]token['"]\s+value=['"]([^'"]+)''',html)[1]
        response=browser.open(url,urllib.parse.urlencode(fields).encode(),timeout=15)
        html=response.read()
        check('bridge login fields authenticate to MariaDB',b'Logout' in html or b'Log out' in html)
        check('HTTP application reports 6.3.0',b'6.3.0' in html)
        return browser,response.url
    try:
        text=(RUNTIME/'compose.yaml').read_text()
        text=re.sub(r'    image: \$\{ADMINER_IMAGE_REF:[^\n]+', '    image: '+OLD, text)
        (root/'compose.yaml').write_text(text)
        shutil.copytree(RUNTIME/'docker/mariadb',root/'docker/mariadb')
        (root/'.env').write_text('\n'.join(['COMPOSE_PROJECT_NAME='+root.name,'PHP_VERSION=8.4',
            'ADMINER_PORT=0','WEB_PORT=0','MAILPIT_PORT=0','DOMAIN=test.local',
            'WP_HOME=http://test.local','WP_SITEURL=http://test.local','DB_NAME=wordpress',
            'DB_USER=wpuser','DB_PASS=smoke-only','DB_ROOT_PASS=smoke-root-only','REDIS_PASSWORD=unused'])+'\n')
        call('up','-d','--wait','--wait-timeout','180','db','adminer')
        old=info('adminer')['Image'];db=info('db')
        check('legacy image starts',images.image_details(old)['adminer_version'].startswith('5.'))
        Path(root/'image-state').mkdir()
        (root/'image-state/adminer.json').write_text(json.dumps({'reference':OLD,'php_full_version':'8.4.27'}))
        try:images.update(root,'adminer')
        except RuntimeError:check('old prepared state requests new preparation')
        else:raise AssertionError('old candidate accepted')
        # Production preparation follows series 6; release acceptance fixes the baseline tag.
        with mock.patch.object(images,'ADMINER_REFRESH_TAG','adminer:6.3.0'):
            prepared=images.prepare_adminer()
        check('candidate is immutable Adminer 6.3.0',prepared['adminer_version']=='6.3.0' and '@sha256:' in prepared['reference'])
        result=images.update(root,'adminer')
        check('legacy Compose applies candidate',result['status']=='patched' and info('adminer')['Image']==prepared['image_id'])
        check('database container is unchanged',info('db')['Id']==db['Id'] and info('db')['State']['StartedAt']==db['State']['StartedAt'])
        login()
        started=info('adminer')['State']['StartedAt']
        check('repeat application is a no-op',images.update(root,'adminer')['status']=='unchanged' and info('adminer')['State']['StartedAt']==started)
        # Restore the prior exact image and inject a readiness failure into the next apply.
        env=(root/'.env').read_text()
        images.write_atomic(root/'.env',images.replace_env(env,'ADMINER_IMAGE_REF',OLD))
        call('up','-d','--no-deps','--force-recreate','--wait','adminer')
        real_ready=images.ready;attempts=[]
        def fail_once(*args):
            attempts.append(True)
            if len(attempts)==1:raise RuntimeError('Injected HTTP readiness failure')
            return real_ready(*args)
        with mock.patch.object(images,'ready',side_effect=fail_once):
            try:images.update(root,'adminer')
            except RuntimeError:pass
            else:raise AssertionError('injected failure accepted')
        check('failed verification restores exact old image',info('adminer')['Image']==old)
        call('stop','adminer')
        before=info('adminer')
        check('stopped service only selects candidate',images.update(root,'adminer')['status']=='selected')
        check('stopped container stays stopped and unchanged',info('adminer')['Id']==before['Id'] and not info('adminer')['State']['Running'])
        call('up','-d','--no-deps','--wait','adminer')
        check('next start uses selected image',info('adminer')['Image']==prepared['image_id'])
        login()
        query='$m=new mysqli("db","wpuser","smoke-only","wordpress");$m->query("CREATE TABLE IF NOT EXISTS adminer_smoke (id INT PRIMARY KEY, value VARCHAR(50))");$m->query("REPLACE INTO adminer_smoke VALUES (1,\\\"verified\\\")");echo $m->query("SELECT value FROM adminer_smoke WHERE id=1")->fetch_row()[0];'
        check('Adminer PHP can modify and query MariaDB',call('exec','-T','adminer','php','-r',query)=='verified')
        print(json.dumps({'checks':results,'candidate':prepared},indent=2),flush=True)
    finally:
        subprocess.run(['docker','compose','-p',root.name,'down','-v','--remove-orphans'],cwd=root,check=False)
        shutil.rmtree(root)


if __name__=='__main__':main()
