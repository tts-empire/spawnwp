#!/usr/bin/env python3
"""Isolated real-container contract test; never uses /srv or production volumes."""
import argparse
import json
import hashlib
import zipfile
import http.cookiejar
import urllib.parse
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

RUNTIME=Path(__file__).resolve().parents[2]
REQUIRED={'bcmath','calendar','curl','dom','exif','fileinfo','ftp','gd','gmp','imagick','intl','json','mbstring',
          'mysqli','mysqlnd','openssl','pcntl','PDO','pdo_mysql','pdo_sqlite','redis','SimpleXML','soap','sockets',
          'sodium','sqlite3','xml','xmlreader','xmlwriter','zip','Zend OPcache'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',required=True)
    parser.add_argument('--php',required=True)
    parser.add_argument('--benchmark-only',action='store_true')
    parser.add_argument('--keep-on-failure',action='store_true')
    parser.add_argument('--report',type=Path,default=Path('/tmp/spawnwp-runtime-smoke.json'))
    args=parser.parse_args()
    root=Path(tempfile.mkdtemp(prefix='spawnwp-runtime-smoke-'))
    name=root.name
    metadata=json.loads(subprocess.check_output(['docker','image','inspect',args.image],text=True))[0]
    report={'image':args.image,'image_id':metadata['Id'],'architecture':metadata['Architecture'],'size_bytes':metadata['Size'],
            'php':args.php,'checks':[],'started_at':time.time()}
    deploy_started=time.monotonic()
    compose=['docker','compose','--project-directory',str(root),'-p',name]
    def call(*command,**kw):
        result=subprocess.run([*compose,*command],text=True,capture_output=True,**kw)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode,result.args,output=result.stdout,stderr=result.stderr)
        if result.stderr:
            report.setdefault('command_warnings',[]).append(result.stderr[-1200:])
        return result.stdout.strip()
    def php(*command): return call('exec','-T','-u','www-data','php',*command)
    def restart_php():
        call('restart','php')
        for _ in range(40):
            probe=subprocess.run([*compose,'exec','-T','php','fpm-healthcheck'],capture_output=True)
            if probe.returncode==0:return
            time.sleep(0.25)
        raise RuntimeError('FPM did not become ready after restart')
    def check(label,condition=True):
        if not condition: raise AssertionError(label)
        report['checks'].append(label)
        print('PASS '+label,flush=True)
    try:
        shutil.copy(RUNTIME/'compose.yaml',root/'compose.yaml')
        shutil.copytree(RUNTIME/'docker',root/'docker')
        content=root/'projects/primary/wp-content';content.mkdir(parents=True)
        os.chown(content,33,33)
        (root/'.env').write_text('\n'.join([
            'COMPOSE_PROJECT_NAME='+name,'PHP_VERSION='+args.php,'PHP_IMAGE_REF='+args.image,
            'WEB_PORT=0','MAILPIT_PORT=0','ADMINER_PORT=0','DOMAIN=test.local',
            'WP_HOME=http://test.local','WP_SITEURL=http://test.local','DB_NAME=wordpress',
            'DB_USER=wpuser','DB_PASS=smoke-only','DB_ROOT_PASS=smoke-root-only',
            'REDIS_PASSWORD=smoke-redis-only','MAILPIT_WEBROOT=/smoke-mail','WP_ADMIN_USER=smoke','WP_ADMIN_EMAIL=smoke@example.invalid'])+'\n')
        (root/'.env').chmod(0o600)
        print('Starting isolated stack '+name,flush=True)
        call('--profile','redis','up','-d','--no-build','--wait','--wait-timeout','240',timeout=360)
        check('all Compose services healthy under production resource/capability limits')
        php('wp','core','install','--url=http://test.local','--title=Smoke','--admin_user=smoke',
            '--admin_password=smoke-only-password','--admin_email=smoke@example.invalid','--skip-email')
        php('wp','rewrite','structure','/%postname%/')
        report['deploy_seconds']=time.monotonic()-deploy_started
        report['wordpress_version']=php('wp','core','version')
        check('WordPress bootstrap and permalinks')
        if args.benchmark_only:
            port=call('port','nginx','80').rsplit(':',1)[1]
            request=urllib.request.Request('http://127.0.0.1:'+port+'/',headers={'Host':'test.local'})
            with urllib.request.urlopen(request,timeout=20) as response:
                check('benchmark HTTP readiness',response.status==200)
            report['deploy_seconds']=time.monotonic()-deploy_started
            report['result']='passed'
            return
        mu=content/'mu-plugins';mu.mkdir(exist_ok=True)
        shutil.copy(RUNTIME/'scripts/mail-capture.php',mu/'mail-capture.php')
        fixture=content/'spawnwp-runtime-test.php'
        fixture.write_text('''<?php
require dirname(__DIR__).'/wp-load.php';
$r=new Redis();$r->connect('redis',6379);$r->auth('smoke-redis-only');$r->set('smoke','ok');
echo json_encode(['version'=>PHP_VERSION,'extensions'=>get_loaded_extensions(),
 'db'=>$GLOBALS['wpdb']->get_var('SELECT 1'),'redis'=>$r->get('smoke'),
 'memory_limit'=>ini_get('memory_limit'),'gd'=>gd_info(),
 'ftps'=>function_exists('ftp_ssl_connect'),'xdebug'=>extension_loaded('xdebug')]);
''')
        port=call('port','nginx','80').rsplit(':',1)[1]
        def get(path):
            request=urllib.request.Request('http://127.0.0.1:'+port+path,headers={'Host':'test.local'})
            with urllib.request.urlopen(request,timeout=30) as r: return r.read()
        data=json.loads(get('/wp-content/spawnwp-runtime-test.php'))
        check('FPM version',data['version'].startswith(args.php+'.'))
        check('FPM extension parity',REQUIRED.issubset(data['extensions']))
        check('FPM to MariaDB',str(data['db'])=='1')
        check('FPM to authenticated Redis',data['redis']=='ok')
        check('FTPS available',data['ftps'])
        check('GD JPEG/WebP',data['gd']['JPEG Support'] and data['gd']['WebP Support'])
        check('Xdebug off by default',not data['xdebug'])
        cli=json.loads(php('php','-r','echo json_encode(get_loaded_extensions());'))
        check('CLI extension parity',REQUIRED.issubset(cli))
        check('HTTP front page',b'<html' in get('/').lower())
        check('REST API',json.loads(get('/wp-json/'))['name']=='Smoke')
        check('WP CLI database query',php('wp','db','query','SELECT 1','--skip-column-names')=='1')
        php('wp','eval','if(!wp_mail("smoke@example.invalid","SpawnWP smoke","Captured only"))exit(1);')
        mailport=call('port','mailpit','8025').rsplit(':',1)[1]
        with urllib.request.urlopen('http://127.0.0.1:'+mailport+'/smoke-mail/api/v1/messages',timeout=15) as r:
            messages=json.load(r)
        check('Mailpit captures wp_mail under webroot',messages['total']>=1)
        adminport=call('port','adminer','8080').rsplit(':',1)[1]
        with urllib.request.urlopen('http://127.0.0.1:'+adminport,timeout=15) as r:
            check('Adminer HTTP UI',r.status==200 and b'Adminer' in r.read())
        cookiejar=http.cookiejar.CookieJar()
        browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookiejar))
        adminurl='http://127.0.0.1:'+adminport+'/'
        browser.open(adminurl,timeout=15).read()
        credentials=urllib.parse.urlencode({'auth[driver]':'server','auth[server]':'db',
                    'auth[username]':'wpuser','auth[password]':'smoke-only','auth[db]':'wordpress'}).encode()
        with browser.open(adminurl,credentials,timeout=15) as response: adminhtml=response.read()
        check('Adminer authenticates to MariaDB',b'Logout' in adminhtml or b'Log out' in adminhtml)
        check('Imagick processes an image',php('php','-r',
            '$i=new Imagick();$i->newImage(8,8,"white");$i->setImageFormat("png");echo strlen($i->getImagesBlob())>0?"ok":"bad";')=='ok')
        check('Intl formats dates',php('php','-r','echo (new IntlDateFormatter("it_IT",0,0))->format(0)!==false?"ok":"bad";')=='ok')
        shutil.copytree(RUNTIME/'scripts',root/'scripts')
        backup=subprocess.check_output(['python3',str(root/'scripts/backup-runtime.py')],cwd=root,text=True).strip()
        backupdir=Path(backup.split('Migration backup: ',1)[1])
        check('complete migration backup',all((backupdir/f).is_file() for f in
              ['database.sql.gz','wordpress.tar.gz','mailpit.db','complete.json']))
        php('wp','option','update','spawnwp_smoke_restore','before')
        subprocess.run(['bash','scripts/snapshot.sh','runtime-smoke'],cwd=root,check=True,capture_output=True)
        php('wp','option','update','spawnwp_smoke_restore','after')
        subprocess.run(['bash','scripts/restore.sh','runtime-smoke'],cwd=root,check=True,capture_output=True)
        check('database snapshot and restore',php('wp','option','get','spawnwp_smoke_restore')=='before')
        for command in [('wp','--version'),('composer','--version'),('node','--version'),('npm','--version'),
                        ('phpcs','--version'),('phpcbf','--version'),('phpstan','--version')]:
            check('tool '+command[0],bool(php(*command)))
        check('WPCS and PHPCompatibilityWP',all(s in php('phpcs','-i') for s in ['WordPress','PHPCompatibilityWP']))
        qa=content/'runtime-qa.php'
        qa.write_text('<?php\nfunction spawnwp_smoke_title(): string { return esc_html("test"); }\n')
        os.chown(qa,33,33)
        lint=subprocess.run([*compose,'exec','-T','-u','www-data','php','phpcs','--standard=WordPress',
                             '--report=json','/var/www/html/wp-content/runtime-qa.php'],capture_output=True,text=True)
        findings=json.loads(lint.stdout)
        check('PHPCS executes WPCS on source',findings['totals']['fixable']>0)
        before=qa.read_text()
        fix=subprocess.run([*compose,'exec','-T','-u','www-data','php','phpcbf','--standard=WordPress',
                            '/var/www/html/wp-content/runtime-qa.php'],capture_output=True,text=True)
        check('PHPCBF fixes source',qa.read_text()!=before and fix.returncode in [0,1,2])
        analysis=php('phpstan','analyse','--configuration=/usr/local/etc/phpstan-wp.neon',
                     '--memory-limit=320M','--no-progress','--error-format=json','/var/www/html/wp-content/runtime-qa.php')
        check('PHPStan analyses WordPress function calls',json.loads(analysis)['totals']['errors']==0)
        qa.unlink()
        (root/'docker/php/zz-site.ini').write_text('memory_limit=192M\nupload_max_filesize=32M\npost_max_size=32M\n')
        restart_php()
        for _ in range(30):
            try:
                data=json.loads(get('/wp-content/spawnwp-runtime-test.php'));break
            except Exception: time.sleep(1)
        check('per-site INI visible to FPM',data['memory_limit']=='192M')
        check('per-site INI visible to CLI',php('php','-r','echo ini_get("memory_limit");')=='192M')
        xdebug=root/'docker/php/zz-xdebug.ini'
        xdebug.write_text('zend_extension=xdebug\nxdebug.mode=debug\nxdebug.start_with_request=no\n')
        restart_php()
        check('Xdebug toggle loads without rebuilding',php('php','-r','echo extension_loaded("xdebug")?"yes":"no";')=='yes')
        debugfile=content/'spawnwp-xdebug-test.php';debugfile.write_text('<?php\n$value=1;\n$value++;\necho $value;\n')
        xdebug.write_text('zend_extension=xdebug\nxdebug.mode=debug\nxdebug.start_with_request=trigger\n'
                          'xdebug.trigger_value=spawnwp-smoke\nxdebug.client_host=debugger\n'
                          'xdebug.client_port=9003\nxdebug.log=/tmp/spawnwp-xdebug.log\n')
        restart_php()
        network=json.loads(call('config','--format','json'))['networks']['internal']['name']
        debugger_name=name+'-debugger'
        debugger=subprocess.Popen(['docker','run','--rm','--name',debugger_name,'--network',network,
            '--network-alias','debugger','--read-only','--user','33:33','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--memory','128m','--pids-limit','16',
            '--mount','type=bind,source='+str(RUNTIME/'tests/integration/fixtures/debugger.php')+',target=/debugger.php,readonly',
            '--entrypoint','php',args.image,'/debugger.php'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            if debugger.stdout.readline().strip()!='ready':raise RuntimeError('Debugger fixture did not start')
            check('Xdebug FPM breakpoint response',get('/wp-content/spawnwp-xdebug-test.php?XDEBUG_TRIGGER=spawnwp-smoke')==b'2')
            stdout,stderr=debugger.communicate(timeout=30)
            check('Xdebug FPM stops at breakpoint',debugger.returncode==0 and 'breakpoint reached' in stdout)
        finally:
            subprocess.run(['docker','rm','-f',debugger_name],capture_output=True)
            if debugger.poll() is None:debugger.terminate();debugger.wait(timeout=10)
        debugfile.unlink()
        xdebug.write_text('; disabled\n');restart_php()
        check('Xdebug toggle off',php('php','-r','echo extension_loaded("xdebug")?"yes":"no";')=='no')
        call('up','-d','--no-deps','--force-recreate','mailpit')
        for _ in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:'+mailport+'/smoke-mail/api/v1/messages',timeout=3) as r: messages=json.load(r)
                break
            except Exception:
                mailport=call('port','mailpit','8025').rsplit(':',1)[1];time.sleep(1)
        check('Mailpit persists messages across recreation',messages['total']>=1)
        (root/'.spawnwp').mkdir(exist_ok=True)
        def apply(manifest):
            path=root/'.spawnwp/blueprint.json';path.write_text(json.dumps(manifest))
            subprocess.run(['bash','scripts/apply-blueprint.sh',str(path)],cwd=root,check=True,capture_output=True)
        for profile in ('clean','demo'):
            manifest=json.loads((RUNTIME/'blueprints'/f'{profile}.json').read_text())
            manifest['source']='built-in'
            apply(manifest)
            check('blueprint '+profile,php('wp','option','get','spawnwp_blueprint_id')==profile)
        php('wp','post','create','--post_status=publish','--post_title=Imported UTF8 🧪',
            '--post_content=https://blueprint.spawnwp.invalid/example/')
        php('wp','eval','update_option("spawnwp_import_array", ["url"=>"https://blueprint.spawnwp.invalid/serialized/"]);')
        export=content/'export.php' 
        export.write_text('<?php\nglobal $wpdb;\n$fp=fopen(\'/var/www/html/wp-content/export.jsonl\',\'w\');\nforeach($wpdb->get_col(\'SHOW TABLES\') as $table) {\n $ddl=$wpdb->get_row(\'SHOW CREATE TABLE `\'.$table.\'`\',ARRAY_N);\n fwrite($fp,json_encode([\'type\'=>\'table\',\'name\'=>$table,\'create\'=>$ddl[1]])."\\n");\n foreach($wpdb->get_results(\'SELECT * FROM `\'.$table.\'`\',ARRAY_A) as $row) {\n  $data=[];foreach($row as $k=>$v)$data[$k]=$v===null?null:base64_encode((string)$v);\n  fwrite($fp,json_encode([\'type\'=>\'row\',\'table\'=>$table,\'data\'=>$data])."\\n");\n }\n}fclose($fp);\n')
        php('wp','eval-file','/var/www/html/wp-content/export.php')
        payload=root/'.spawnwp/payload.zip'
        with zipfile.ZipFile(payload,'w') as archive:
            archive.write(content/'export.jsonl','database.jsonl')
            archive.writestr('content/plugins/runtime-fixture/runtime-fixture.php',
                             '<?php /* Plugin Name: Runtime Fixture */')
            archive.writestr('content/uploads/runtime-fixture.txt','persisted upload')
        (content/'export.jsonl').unlink();export.unlink()
        (mu/'mail-capture.php').unlink()
        manifest={'schema_version':2,'id':'runtime-import','name':'Runtime import','version':'1.0.0',
                  'theme':'','source':'custom','wporg_plugins':[],
                  'capture':{'plugins':True,'themes':False,'uploads':True,'database':True},
                  'payload_path':str(payload),'payload':{'sha256':hashlib.sha256(payload.read_bytes()).hexdigest()}}
        apply(manifest)
        check('content blueprint database import',php('wp','option','get','spawnwp_blueprint_id')=='runtime-import')
        imported=json.loads(php('wp','option','get','spawnwp_import_array','--format=json'))
        check('serialized URLs survive import/rewrite',imported['url']=='http://test.local/serialized/')
        check('content blueprint uploaded files',(content/'uploads/runtime-fixture.txt').read_text()=='persisted upload')
        check('content blueprint mail capture installed',(mu/'mail-capture.php').is_file())
        php('wp','eval','if(!wp_mail("smoke@example.invalid","Imported blueprint smoke","Captured only"))exit(1);')
        manifest=json.loads((RUNTIME/'blueprints/development.json').read_text());manifest['source']='built-in'
        apply(manifest)
        check('development blueprint and toolkit',(mu/'devkit.php').is_file())
        report['result']='passed'
    except Exception as exc:
        report['result']='failed';report['error']=str(exc);report['command_output']=getattr(exc,'output','');report['command_stderr']=getattr(exc,'stderr','')
        try:
            report['debugger_log']=call('exec','-T','php','sh','-c','cat /tmp/spawnwp-xdebug.log 2>/dev/null || true')
            report['logs']=call('logs','--no-color','--tail','60')
        except Exception: pass
        raise
    finally:
        report['finished_at']=time.time()
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n')
        if args.keep_on_failure and report.get('result')=='failed':
            print('Retained test project: '+str(root),flush=True)
        else:
            subprocess.run([*compose,'--profile','redis','down','-v','--remove-orphans'],check=False)
            shutil.rmtree(root)

if __name__=='__main__': main()
