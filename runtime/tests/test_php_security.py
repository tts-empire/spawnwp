import json
import unittest
from types import SimpleNamespace
from unittest import mock
import php_security as security


class PhpSecurityTests(unittest.TestCase):
    def test_fixed_release_floors(self):
        for version in ('8.2.35','8.3.36','8.4.27','8.5.12'):
            self.assertEqual('patched',security.status(version)['status'])

    def test_pre_fix_and_legacy_numbers_do_not_prove_backports(self):
        for version in ('8.2.34','8.3.35','8.4.26','8.5.11','7.4.33'):
            self.assertEqual('vulnerable',security.status(version)['status'])
        for version in (None,'8.4.27-custom'):
            self.assertEqual('unverified',security.status(version)['status'])

    def legacy(self):
        return {'php_full_version':'7.4.33','php_series':'7.4','os_id':'debian','os_version':'13',
                'binary_integrity':True,'runtime_package_version':security.SURY_VERSION,
                'core_packages':{k:security.SURY_VERSION for k in ['php7.4-cli','php7.4-fpm','php7.4-common']}}

    def test_legacy_provenance_proves_each_cve_but_remains_eol(self):
        report=security.assess(self.legacy(),security.POLICY,'7.4')
        self.assertEqual('patched',report['status'])
        self.assertTrue(report['legacy'])
        self.assertEqual({c:'patched' for c in security.CVES},report['cve_fixes'])

    def test_unknown_or_modified_legacy_packages_fail_closed(self):
        for change in ({'os_version':'11'},{'binary_integrity':False},{'core_packages':{}},
                       {'core_packages':{'php7.4-cli':'7.4.33-unknown'}},{'php_full_version':'7.4.32'}):
            with self.subTest(change=change):
                self.assertEqual('unverified',security.assess({**self.legacy(),**change},security.POLICY,'7.4')['status'])
        self.assertEqual('unverified',security.assess(self.legacy(),security.OLD_POLICY,'7.4')['status'])

    def test_probe_uses_resolved_image_identity_not_moving_tag(self):
        info=[{'Id':'sha256:abc','Config':{'Labels':{
            'org.spawnwp.php-series':'8.4','org.spawnwp.php-security-policy':security.POLICY}}}]
        probe={'php_full_version':'8.4.27','php_series':'8.4','os_id':'debian','os_version':'13'}
        responses=[SimpleNamespace(returncode=0,stdout=json.dumps(info)),
                   SimpleNamespace(returncode=0,stdout=json.dumps(probe))]
        with mock.patch.object(security.subprocess,'run',side_effect=responses) as run:
            report=security.image_report('wp-dev-php:8.4')
        self.assertEqual('patched',report['status'])
        self.assertIn('sha256:abc',run.call_args.args[0])
        self.assertNotIn('wp-dev-php:8.4',run.call_args.args[0])

    def test_eol_acceptance_is_still_scoped_to_exact_image(self):
        with mock.patch.object(security,'image_report',return_value={
                'status':'patched','legacy':True,'image_id':'sha256:legacy'}):
            self.assertFalse(security.accepts_image('legacy',False,'sha256:legacy'))
            self.assertFalse(security.accepts_image('legacy',True,'sha256:other'))
            self.assertTrue(security.accepts_image('legacy',True,'sha256:legacy'))

    def test_missing_image_cannot_be_accepted(self):
        with mock.patch.object(security.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='')):
            self.assertFalse(security.accepts_image('missing',True,''))

if __name__=='__main__':unittest.main()
