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

def browser_login(root, adminurl, check):
    import ast
    import threading
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    from playwright.sync_api import sync_playwright
    tree=ast.parse((RUNTIME/'app.py').read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='db_login')
    function.decorator_list=[]
    module=ast.Module(body=[function],type_ignores=[])
    namespace={'json':json,'resolve_project':lambda name:root,
               '_read_env':lambda path:{'DB_USER':'wpuser','DB_PASS':'smoke-only','DB_NAME':'wordpress'},
               'HTMLResponse':lambda content,**kwargs:content}
    exec(compile(module,'actual-cockpit-db-login','exec'),namespace)
    bridge=namespace['db_login']('smoke')
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.proxy()
        def do_POST(self):self.proxy()
        def proxy(self):
            if self.path=='/bridge':
                data=bridge.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(data);return
            if not self.path.startswith('/smoke-db/'):
                self.send_error(404);return
            url=adminurl+self.path[len('/smoke-db/'):]
            data=self.rfile.read(int(self.headers.get('Content-Length',0))) if self.command=='POST' else None
            headers={k:v for k,v in self.headers.items() if k.lower() in ('cookie','content-type','referer')}
            opener=urllib.request.build_opener(images.NoRedirect())
            try:response=opener.open(urllib.request.Request(url,data=data,headers=headers),timeout=20)
            except urllib.error.HTTPError as error:response=error
            body=response.read();self.send_response(response.code)
            for key,value in response.headers.items():
                if key.lower() not in ('transfer-encoding','connection','content-length'):
                    self.send_header(key,value)
            self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            executable=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
            browser=p.chromium.launch(headless=True,executable_path=executable,args=['--no-sandbox'])
            page=browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/bridge')
            page.get_by_text('Logout',exact=True).wait_for(timeout=20000)
            check('actual Cockpit DB bridge logs in through subpath in Chromium')
            base=f'http://127.0.0.1:{server.server_port}/smoke-db/'
            def execute_sql(query):
                page.goto(base+'?server=db&username=wpuser&db=wordpress&sql=')
                with page.expect_navigation():
                    page.locator('textarea[name="query"]').evaluate('(e, sql) => { e.value = sql; HTMLFormElement.prototype.submit.call(e.form); }', query)
            execute_sql("CREATE TABLE IF NOT EXISTS browser_smoke (id INT PRIMARY KEY, value VARCHAR(50)); REPLACE INTO browser_smoke VALUES (1, 'browser verified'); SELECT * FROM browser_smoke;")
            page.get_by_text('browser verified',exact=True).wait_for()
            check('Adminer SQL UI modifies and queries MariaDB')
            page.goto(base+'?server=db&username=wpuser&db=wordpress&dump=')
            page.locator('input[name="output"][value="file"]').check()
            page.locator('input[name="tables[]"][value="browser_smoke"]').check()
            page.locator('input[name="data[]"][value="browser_smoke"]').check()
            with page.expect_download() as download:
                page.get_by_role('button',name='Export',exact=True).click()
            export=root/'browser-smoke.sql';download.value.save_as(export)
            check('Adminer exports SQL with data', 'browser_smoke' in export.read_text() and 'browser verified' in export.read_text())
            execute_sql('DROP TABLE browser_smoke;')
            page.goto(base+'?server=db&username=wpuser&db=wordpress&import=')
            page.locator('input[name="sql_file[]"]').set_input_files(export)
            with page.expect_navigation():
                page.get_by_role('button',name='Execute',exact=True).click()
            execute_sql('SELECT * FROM browser_smoke;')
            page.get_by_text('browser verified',exact=True).wait_for()
            check('Adminer imports exported SQL and restores data')
            page.get_by_text('Logout',exact=True).click()
            page.locator('input[name="auth[username]"]').wait_for()
            check('Adminer logout returns to login form')
            browser.close()
    finally:
        server.shutdown();server.server_close()


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
        if os.environ.get('SPAWNWP_TEST_BROWSER'):
            browser_login(root,'http://127.0.0.1:'+call('port','adminer','8080').rsplit(':',1)[-1]+'/',check)
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
