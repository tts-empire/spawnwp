import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import service_images as images

OLD = 'sha256:' + 'a' * 64
NEW = 'sha256:' + 'b' * 64
REF = 'adminer@sha256:' + 'c' * 64
LEGACY = 'services:\n  adminer:\n    image: adminer:5\n    ports: ["127.0.0.1:9001:8080"]\n  db:\n    image: mariadb:10.11\n'


class ServiceImagesTests(unittest.TestCase):
    def test_legacy_conversion_changes_only_adminer_image(self):
        converted = images.managed_adminer_compose(LEGACY)
        self.assertIn('image: ${ADMINER_IMAGE_REF:-adminer:5}', converted)
        self.assertIn('    image: mariadb:10.11', converted)
        self.assertEqual(converted, images.managed_adminer_compose(converted))

    def test_custom_and_ambiguous_images_require_review(self):
        for text in [LEGACY.replace('adminer:5', 'custom/dbui:5'), 'services:\n  adminer: *custom\n', LEGACY.replace('    image: adminer:5', '    image: *custom')]:
            with self.assertRaises(RuntimeError):images.managed_adminer_compose(text)

    def test_old_and_invalid_prepared_state_require_preparation(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, SPAWNWP_IMAGE_STATE_ROOT=tmp):
            self.assertEqual(images.prepared_adminer()['status'], 'not_prepared')
            for record in [{'reference': REF}, {'reference': REF, 'adminer_version': '5.5.1'}, {'reference': 'adminer:6', 'adminer_version': '6.3.0'}]:
                Path(tmp, 'adminer.json').write_text(json.dumps(record))
                self.assertEqual(images.prepared_adminer()['status'], 'prepare_required')

    def test_version_policy_and_php_gate(self):
        for version in ['unknown', '5.5.1', '6.2.0', '7.0.0']:
            with mock.patch.object(images, 'image_details', return_value={'image_id': NEW, 'adminer_version': version}):
                with self.assertRaises(RuntimeError):images.verify_adminer(REF)
        with mock.patch.object(images, 'image_details', return_value={'image_id': NEW, 'adminer_version': '6.3.0'}), mock.patch.object(images, 'php_version', return_value='8.4.1'):
            with self.assertRaises(RuntimeError):images.verify_adminer(REF)

    def test_failed_preparation_preserves_previous_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, SPAWNWP_IMAGE_STATE_ROOT=tmp):
            path=Path(tmp, 'adminer.json');path.write_text('previous')
            with mock.patch.object(images, 'docker', side_effect=[json.dumps({'digest':'sha256:'+'c'*64}), '']), mock.patch.object(images, 'verify_adminer', side_effect=RuntimeError('unpatched')):
                with self.assertRaises(RuntimeError):images.prepare_adminer()
            self.assertEqual(path.read_text(), 'previous')

    def transaction(self, *, running=True, already=False, override=False, failure=False):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)/'site';project.mkdir()
            (project/'.env').write_text('CUSTOM=keep\n')
            (project/'compose.yaml').write_text(LEGACY)
            def cfg(_):
                env=dict(line.split('=',1) for line in (project/'.env').read_text().splitlines() if '=' in line)
                image='adminer:5'
                if '${ADMINER_IMAGE_REF:-' in (project/'compose.yaml').read_text() and not override:
                    image=env.get('ADMINER_IMAGE_REF',image)
                return {'services':{'adminer':{'image':image},'db':{'image':'mariadb:10.11'}}}
            current={'Image':NEW if already else OLD, 'State':{'Running':running}}
            def docker(*args):
                if args[0]=='inspect':return json.dumps([current])
                if args[:2]==('image','inspect'):return NEW
                return ''
            with mock.patch.dict(os.environ, SPAWNWP_IMAGE_LOCK_ROOT=tmp), mock.patch.object(images, 'candidate', return_value=REF), mock.patch.object(images, 'verify_adminer',return_value={'image_id':NEW,'adminer_version':'6.3.0','php_full_version':'8.4.27'}), mock.patch.object(images,'config',side_effect=cfg), mock.patch.object(images,'docker',side_effect=docker), mock.patch.object(images,'compose',side_effect=lambda p,*a:'cid' if a[0]=='ps' else '') as calls, mock.patch.object(images,'ready',side_effect=[RuntimeError('wrong running image'),None] if failure else None) as ready:
                if override or failure:
                    with self.assertRaises(RuntimeError):images.update(project,'adminer')
                else:
                    result=images.update(project,'adminer')
                    self.assertEqual(result['status'],'selected' if not running else 'unchanged' if already else 'patched')
                    self.assertEqual(cfg(project)['services']['adminer']['image'],REF)
                starts=[c for c in calls.call_args_list if c.args[1]=='up']
                self.assertEqual(len(starts),2 if failure else 0 if override or not running or already else 1)
                self.assertIn('CUSTOM=keep',(project/'.env').read_text())
                if override:
                    self.assertEqual((project/'compose.yaml').read_text(),LEGACY)
                    self.assertEqual((project/'.env').read_text(),'CUSTOM=keep\n')
                if failure:
                    self.assertIn('spawnwp-retained:adminer-',(project/'.env').read_text())
                    self.assertEqual(ready.call_args.args[2],OLD)

    def test_legacy_apply_verifies_expected_image(self):self.transaction()
    def test_environment_override_is_rejected_before_recreation(self):self.transaction(override=True)
    def test_down_site_is_only_selected(self):self.transaction(running=False)
    def test_same_image_is_not_recreated(self):self.transaction(already=True)
    def test_failed_readiness_restores_previous_image(self):self.transaction(failure=True)

    def test_readiness_rejects_healthy_wrong_image(self):
        with mock.patch.object(images,'compose',return_value='cid'), mock.patch.object(images,'docker',return_value=json.dumps([{'Image':OLD,'State':{'Status':'running','Health':{'Status':'healthy'}}}])):
            with self.assertRaisesRegex(RuntimeError,'does not match'):images.ready(Path('.'),'adminer',NEW)


if __name__ == '__main__':unittest.main()
