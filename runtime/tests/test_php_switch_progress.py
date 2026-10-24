import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout, ExitStack
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

path=Path(__file__).parents[1]/'scripts/php-switch-progress.py'
spec=importlib.util.spec_from_file_location('php_switch_progress',path)
progress=importlib.util.module_from_spec(spec);spec.loader.exec_module(progress)
OLD='wp-dev-php:8.3-trixie-aaaaaaaaaaaa'
NEW='wp-dev-php:8.4-trixie-bbbbbbbbbbbb'

class PhpSwitchProgressTests(unittest.TestCase):
    def fixture(self,root):
        project=root/'site';project.mkdir();(project/'compose.yaml').write_text('services: {}\n')
        (project/'.env').write_text('PHP_VERSION=8.3\nWP_VERSION=7.0.1\nPHP_IMAGE_REF='+OLD+'\n')
        (project/'.spawnwp').mkdir();(project/'.spawnwp/runtime-migrated.json').write_text('{}')
        return project

    def mocks(self,running=True):
        stack=ExitStack()
        def run(args,**kwargs):
            return SimpleNamespace(returncode=0,stdout='php\nnginx\n' if args[:3]==['docker','compose','ps'] and running else '')
        stack.enter_context(mock.patch.object(progress.subprocess,'run',side_effect=run))
        stack.enter_context(mock.patch.object(progress.subprocess,'check_output',return_value='sha256:old'))
        stack.enter_context(mock.patch.object(progress,'image_identity',return_value=('wp-dev-php:8.4-wp7.0.1','-wp7.0.1','abc123')))
        stack.enter_context(mock.patch.object(progress,'prepare',return_value=NEW))
        stack.enter_context(mock.patch.object(progress,'preserve',return_value=OLD))
        stack.enter_context(mock.patch.object(progress,'metric_incr'))
        return stack

    def test_supported_versions(self):
        self.assertEqual({'7.4','8.2','8.3','8.4','8.5'},progress.ALLOWED)

    def test_failed_build_does_not_change_selection_or_start_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=self.fixture(root);original=(project/'.env').read_text()
            with self.mocks(),mock.patch.object(progress,'prepare',side_effect=RuntimeError('build failed')),\
                 mock.patch.object(progress,'run_simple') as execute,redirect_stdout(StringIO()):
                self.assertEqual(1,progress.switch(project,'8.4',root/'locks'))
            self.assertEqual(original,(project/'.env').read_text());execute.assert_not_called()

    def test_running_site_checks_http_before_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=self.fixture(root);out=StringIO()
            with self.mocks(),mock.patch.object(progress,'run_simple',return_value=0) as execute,\
                 mock.patch.object(progress,'healthy',return_value=True),\
                 mock.patch.object(progress,'site_ready',return_value=True) as ready,redirect_stdout(out):
                self.assertEqual(0,progress.switch(project,'8.4',root/'locks'))
            self.assertIn('PHP_IMAGE_REF='+NEW,(project/'.env').read_text());ready.assert_called_once()
            self.assertIn('"type":"complete"',out.getvalue())
            self.assertIn('--no-deps',execute.call_args.args[0]);self.assertIn('--no-build',execute.call_args.args[0])
            self.assertIn('WP_IMAGE_SUFFIX=-wp7.0.1',(project/'.env').read_text())

    def test_down_site_remains_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=self.fixture(root)
            with self.mocks(running=False),mock.patch.object(progress,'run_simple') as execute,\
                 mock.patch.object(progress,'site_ready') as ready,redirect_stdout(StringIO()):
                self.assertEqual(0,progress.switch(project,'8.4',root/'locks'))
            execute.assert_not_called();ready.assert_not_called()
            self.assertIn(NEW,(project/'.env').read_text())

    def test_failed_http_rolls_back_exact_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=self.fixture(root)
            with self.mocks(),mock.patch.object(progress,'run_simple',return_value=0) as execute,\
                 mock.patch.object(progress,'healthy',return_value=True),\
                 mock.patch.object(progress,'site_ready',side_effect=[False,True]),redirect_stdout(StringIO()):
                self.assertEqual(1,progress.switch(project,'8.4',root/'locks'))
            self.assertIn('PHP_IMAGE_REF='+OLD,(project/'.env').read_text())
            self.assertIn('PHP_VERSION=8.3',(project/'.env').read_text());self.assertEqual(2,execute.call_count)
            history=json.loads((project/'.spawnwp/runtime-history.json').read_text())
            self.assertEqual(OLD,history[0]['previous_ref'])

    def test_unaccepted_legacy_does_not_prepare_an_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=self.fixture(root)
            with self.mocks(),mock.patch.object(progress,'accepts_image',return_value=False),\
                 mock.patch.object(progress,'prepare') as prepare,redirect_stdout(StringIO()):
                self.assertEqual(2,progress.switch(project,'7.4',root/'locks'))
            prepare.assert_not_called()

if __name__=='__main__':unittest.main()
