import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import runtime_migration as migration


class RuntimeMigrationTests(unittest.TestCase):
    def fixture(self,root):
        template=root/'template';site=root/'site'
        for base in (template,site):(base/'docker/php').mkdir(parents=True)
        (template/'docker/php/zz-xdebug.ini').write_text('; disabled\n')
        (site/'compose.yaml').write_text('old compose\n');(template/'compose.yaml').write_text('new compose\n')
        (site/'.env').write_text('PHP_VERSION=8.3\nPHP_IMAGE_REF=frozen-old-image\nCUSTOM=preserved\n')
        manifest={'managed':{'compose.yaml':[hashlib.sha256(b'old compose\n').hexdigest()]}}
        return template,site,manifest

    def test_prepares_without_starting_containers_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            template,site,manifest=self.fixture(Path(tmp))
            with mock.patch.object(migration.subprocess,'run',return_value=SimpleNamespace(stdout='')) as commands:
                result=migration.stage_site(site,template,manifest)
                self.assertEqual('prepared',result['status'])
                self.assertEqual('unchanged',migration.stage_site(site,template,manifest)['status'])
            self.assertEqual(['docker','compose','ps','-aq'],commands.call_args.args[0])
            self.assertIn('CUSTOM=preserved',(site/'.env').read_text())
            self.assertEqual('old compose\n',(Path(result['backup'])/'compose.yaml').read_text())

    def test_custom_compose_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            template,site,manifest=self.fixture(Path(tmp));(site/'compose.yaml').write_text('custom')
            with mock.patch.object(migration.subprocess,'run') as commands:
                result=migration.stage_site(site,template,manifest)
            self.assertEqual('migration_required',result['status']);commands.assert_not_called()
            self.assertEqual('custom',(site/'compose.yaml').read_text())

    def test_new_release_can_replace_previous_managed_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            template,site,manifest=self.fixture(Path(tmp))
            with mock.patch.object(migration.subprocess,'run',return_value=SimpleNamespace(stdout='')):
                migration.stage_site(site,template,manifest)
                (template/'compose.yaml').write_text('next release compose\n')
                # Time-independent distinct backup directories are required even within one second.
                result=migration.stage_site(site,template,manifest)
            self.assertEqual('prepared',result['status'])
            self.assertEqual('next release compose\n',(site/'compose.yaml').read_text())

    def test_first_upgrade_recovers_ini_overwritten_by_old_updater(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);template,site,manifest=self.fixture(root)
            backups=root/'backups';backup=backups/'transaction';(backup/'runtime/docker/php').mkdir(parents=True)
            (backup/'absent.json').write_text('[]')
            (backup/'runtime/docker/php/zz-site.ini').write_text('memory_limit=768M')
            (backup/'runtime/docker/php/xdebug.ini').write_text('zend_extension=xdebug')
            migration.restore_primary_overrides(template,backups)
            self.assertEqual('memory_limit=768M',(template/'docker/php/zz-site.ini').read_text())
            self.assertEqual('zend_extension=xdebug',(template/'docker/php/zz-xdebug.ini').read_text())

if __name__=='__main__':unittest.main()
