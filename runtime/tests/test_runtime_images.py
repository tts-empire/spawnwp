import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import runtime_images as images


class RuntimeImageTests(unittest.TestCase):
    def test_selection_prefers_immutable_ref_and_preserves_legacy_suffix(self):
        self.assertEqual('wp-dev-php:8.3-wp7.0.1',images.selected({'PHP_VERSION':'8.3','WP_IMAGE_SUFFIX':'-wp7.0.1'}))
        self.assertEqual('frozen',images.selected({'PHP_IMAGE_REF':'frozen','PHP_VERSION':'8.4'}))
        for bad in ('7.1/../oops','latest;command'):
            with self.assertRaises(ValueError):images.alias('8.4',bad)

    def test_down_sites_and_rollback_are_protected_from_gc(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp)/'down-site';site.mkdir();(site/'.spawnwp').mkdir()
            (site/'.env').write_text('PHP_VERSION=8.4\nPHP_IMAGE_REF=wp-dev-php:8.4-trixie-aaaaaaaaaaaa\n')
            (site/'.spawnwp/runtime-history.json').write_text(json.dumps([
                {'previous_ref':'wp-dev-php:8.3-trixie-bbbbbbbbbbbb','retain_until':time.time()+86400},
                {'previous_ref':'wp-dev-php:8.2-trixie-cccccccccccc','retain_until':0}]))
            protected=images.references([site])
            self.assertIn('8.4-trixie-aaaaaaaaaaaa',protected)
            self.assertIn('8.3-trixie-bbbbbbbbbbbb',protected)
            self.assertNotIn('8.2-trixie-cccccccccccc',protected)

    def test_unreadable_retention_state_aborts_gc(self):
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(tmp);(site/'.env').write_text('PHP_VERSION=8.4\n');(site/'.spawnwp').mkdir()
            (site/'.spawnwp/runtime-rollback.json').write_text('broken')
            with self.assertRaises(ValueError):images.references([site])

    def test_failed_build_never_moves_prepared_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);context=project/'docker/php';context.mkdir(parents=True)
            (context/'runtime-lock.json').write_text(json.dumps({'images':{'8.4':'base@sha256:fixed'}}))
            process=SimpleNamespace(stdout=iter(['build failed\n']),wait=lambda:1)
            def docker(*args):
                if args[:2]==('image','inspect'):raise subprocess.CalledProcessError(1,['docker'])
                raise AssertionError('A failed build must not tag/promote anything')
            with mock.patch.dict('os.environ',{'SPAWNWP_IMAGE_LOCK_ROOT':tmp}),\
                 mock.patch.object(images,'docker',side_effect=docker),\
                 mock.patch.object(images.subprocess,'check_output',return_value='recipe'),\
                 mock.patch.object(images.subprocess,'Popen',return_value=process),\
                 mock.patch.object(images.subprocess,'run') as cleanup:
                with self.assertRaisesRegex(RuntimeError,'build failed'):
                    images.prepare(project,'8.4',log=lambda _:None)
            self.assertEqual(['docker','image','rm'],cleanup.call_args.args[0][:3])

    def test_poisoned_cached_image_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);context=project/'docker/php';context.mkdir(parents=True)
            (context/'runtime-lock.json').write_text(json.dumps({'images':{'8.4':'base@sha256:fixed'}}))
            cached=[{'Id':'poisoned','Config':{'Labels':{'org.spawnwp.context-hash':'recipe'}}}]
            process=SimpleNamespace(stdout=iter([]),wait=lambda:1)
            with mock.patch.dict('os.environ',{'SPAWNWP_IMAGE_LOCK_ROOT':tmp}),\
                 mock.patch.object(images,'docker',return_value=json.dumps(cached)),\
                 mock.patch.object(images,'image_report',return_value={'status':'unverified'}),\
                 mock.patch.object(images.subprocess,'check_output',return_value='recipe'),\
                 mock.patch.object(images.subprocess,'Popen',return_value=process) as build,\
                 mock.patch.object(images.subprocess,'run'):
                with self.assertRaises(RuntimeError):images.prepare(project,'8.4',log=lambda _:None)
            build.assert_called_once()

    def test_context_change_during_build_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);context=project/'docker/php';context.mkdir(parents=True)
            (context/'runtime-lock.json').write_text(json.dumps({'images':{'8.4':'base@sha256:fixed'}}))
            process=SimpleNamespace(stdout=iter([]),wait=lambda:0)
            with mock.patch.dict('os.environ',{'SPAWNWP_IMAGE_LOCK_ROOT':tmp}),\
                 mock.patch.object(images,'docker',side_effect=subprocess.CalledProcessError(1,['docker'])),\
                 mock.patch.object(images,'image_report') as report,\
                 mock.patch.object(images.subprocess,'check_output',side_effect=['before','after']),\
                 mock.patch.object(images.subprocess,'Popen',return_value=process),\
                 mock.patch.object(images.subprocess,'run'):
                with self.assertRaisesRegex(RuntimeError,'inputs changed'):
                    images.prepare(project,'8.4',log=lambda _:None)
            report.assert_not_called()

if __name__=='__main__':unittest.main()
