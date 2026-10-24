import hashlib
import json
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import php_security

ROOT=Path(__file__).parents[1]
CONTEXT=ROOT/'docker/php'

class RuntimeSourceTests(unittest.TestCase):
    def test_sury_core_allowlist_and_archives_match(self):
        lock=json.loads((CONTEXT/'runtime-lock.json').read_text())
        self.assertEqual(php_security.SURY_VERSION,lock['sury_core_version'])
        for arch in ['amd64','arm64']:
            downloads=dict(line.split(' ',1)[::-1] for line in (CONTEXT/f'sury-downloads-{arch}.txt').read_text().splitlines())
            for package,record in lock['packages'][arch].items():
                self.assertEqual(record['SHA256'],downloads[record['Filename']])
                self.assertEqual(record['Version'],lock['packages']['amd64'][package]['Version'])
            self.assertEqual(len(downloads),len(lock['packages'][arch]))
        actual=hashlib.sha256((CONTEXT/'sury-archive-keyring.gpg').read_bytes()).hexdigest()
        self.assertEqual(lock['sury_key_sha256'],actual)

    def test_every_copy_input_is_fingerprinted(self):
        manifest=(ROOT/'scripts/lib-image.sh').read_text()
        inputs=set(re.search(r'^SPAWNWP_IMAGE_INPUTS=\((.*)\)$',manifest,re.M)[1].split())
        for dockerfile in ['Dockerfile','Dockerfile.legacy']:
            for line in (CONTEXT/dockerfile).read_text().splitlines():
                if not line.startswith('COPY ') or '--from=' in line:continue
                fields=[f for f in shlex.split(line)[1:] if not f.startswith('--')]
                for name in fields[:-1]:
                    self.assertIn(name,inputs,f'{dockerfile}: missing build input {name}')

    def test_mutable_site_settings_do_not_invalidate_shared_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(CONTEXT,root/'docker/php')
            command=['bash',str(ROOT/'scripts/lib-image.sh'),'hash','7.0.1']
            before=subprocess.check_output(command,cwd=root,text=True)
            for name in ['zz-site.ini','zz-xdebug.ini','xdebug.ini']:
                (root/'docker/php'/name).write_text('changed site configuration')
            self.assertEqual(before,subprocess.check_output(command,cwd=root,text=True))
            with (root/'docker/php/php.ini').open('a') as stream:stream.write('\n; changed default\n')
            self.assertNotEqual(before,subprocess.check_output(command,cwd=root,text=True))

if __name__=='__main__':unittest.main()
