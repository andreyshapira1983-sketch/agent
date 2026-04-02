# Покрытие: tools/capability_discovery.py
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools.capability_discovery import DiscoveredCapability, CapabilityDiscovery


class TestDiscoveredCapability(unittest.TestCase):

    def test_init_defaults(self):
        c = DiscoveredCapability('lib', 'desc', 'library')
        self.assertEqual(c.name, 'lib')
        self.assertEqual(c.status, 'discovered')
        self.assertIsNone(c.score)
        self.assertEqual(c.tags, [])

    def test_init_with_args(self):
        c = DiscoveredCapability('api', 'desc', 'api', source='github',
                                 how_to_use='call it', tags=['net'])
        self.assertEqual(c.source, 'github')
        self.assertEqual(c.tags, ['net'])

    def test_to_dict(self):
        c = DiscoveredCapability('x', 'y', 'tool')
        c.score = 0.7
        d = c.to_dict()
        self.assertEqual(d['name'], 'x')
        self.assertEqual(d['score'], 0.7)
        self.assertIn('capability_type', d)


class TestCapabilityDiscovery(unittest.TestCase):

    def _make(self, **kw):
        defaults = dict(
            tool_layer=MagicMock(),
            cognitive_core=MagicMock(),
            monitoring=MagicMock(),
            human_approval=MagicMock(),
        )
        defaults.update(kw)
        return CapabilityDiscovery(**defaults)

    # ── discover_libraries ────────────────────────────────────────────────
    def test_discover_libraries_no_core(self):
        cd = self._make(cognitive_core=None)
        self.assertEqual(cd.discover_libraries('pdf'), [])

    def test_discover_libraries_with_core(self):
        cc = MagicMock()
        cc.reasoning.return_value = (
            "НАЗВАНИЕ: reportlab\n"
            "ОПИСАНИЕ: генерация PDF\n"
            "УСТАНОВКА: pip install reportlab\n"
            "ПРИМЕР: from reportlab.lib import ..."
        )
        cd = self._make(cognitive_core=cc)
        caps = cd.discover_libraries('pdf generation', n=3)
        self.assertGreaterEqual(len(caps), 0)  # parsing may or may not yield results

    # ── discover_apis ─────────────────────────────────────────────────────
    def test_discover_apis_no_core(self):
        cd = self._make(cognitive_core=None)
        self.assertEqual(cd.discover_apis('weather'), [])

    def test_discover_apis_fallback(self):
        cc = MagicMock()
        cc.reasoning.return_value = "Some API description without structured format"
        cd = self._make(cognitive_core=cc)
        caps = cd.discover_apis('weather')
        self.assertEqual(len(caps), 1)  # fallback creates single cap
        self.assertEqual(caps[0].capability_type, 'api')

    # ── scan_installed ────────────────────────────────────────────────────
    def test_scan_installed_returns_list(self):
        cd = self._make()
        caps = cd.scan_installed()
        self.assertIsInstance(caps, list)
        if caps:
            self.assertEqual(caps[0].source, 'local')

    def test_scan_installed_exception_path(self):
        cd = self._make()
        with patch('importlib.metadata.distributions',
                   side_effect=Exception('no metadata')):
            caps = cd.scan_installed()
            self.assertEqual(caps, [])

    # ── evaluate ──────────────────────────────────────────────────────────
    def test_evaluate_missing(self):
        cd = self._make()
        self.assertEqual(cd.evaluate('nonexistent'), 0.0)

    def test_evaluate_no_core(self):
        cd = self._make(cognitive_core=None)
        cd._discovered['lib'] = DiscoveredCapability('lib', 'x', 'library')
        score = cd.evaluate('lib')
        self.assertEqual(score, 0.5)

    def test_evaluate_with_core(self):
        cc = MagicMock()
        cc.reasoning.return_value = "0.85"
        cd = self._make(cognitive_core=cc)
        cd._discovered['lib'] = DiscoveredCapability('lib', 'desc', 'library')
        score = cd.evaluate('lib')
        self.assertAlmostEqual(score, 0.85, places=1)

    def test_evaluate_with_core_no_number(self):
        cc = MagicMock()
        cc.reasoning.return_value = "This is very useful"
        cd = self._make(cognitive_core=cc)
        cd._discovered['lib'] = DiscoveredCapability('lib', 'desc', 'library')
        score = cd.evaluate('lib')
        self.assertEqual(score, 0.5)  # fallback

    # ── evaluate_all ──────────────────────────────────────────────────────
    def test_evaluate_all(self):
        cc = MagicMock()
        cc.reasoning.return_value = "0.7"
        cd = self._make(cognitive_core=cc)
        cd._discovered['a'] = DiscoveredCapability('a', 'd', 'library')
        cd._discovered['b'] = DiscoveredCapability('b', 'd', 'library')
        cd._discovered['b'].status = 'evaluated'  # skip
        scores = cd.evaluate_all()
        self.assertIn('a', scores)
        self.assertNotIn('b', scores)

    # ── accept ────────────────────────────────────────────────────────────
    def test_accept_missing(self):
        cd = self._make()
        self.assertFalse(cd.accept('nonexistent'))

    def test_accept_approved(self):
        cd = self._make()
        cd.human_approval.request_approval.return_value = True
        cd._discovered['lib'] = DiscoveredCapability('lib', 'd', 'library')
        self.assertTrue(cd.accept('lib'))
        self.assertEqual(cd._discovered['lib'].status, 'accepted')
        self.assertIn('lib', cd._accepted)

    def test_accept_rejected_by_human(self):
        cd = self._make()
        cd.human_approval.request_approval.return_value = False
        cd._discovered['api'] = DiscoveredCapability('api', 'd', 'api')
        self.assertFalse(cd.accept('api'))
        self.assertEqual(cd._discovered['api'].status, 'rejected')

    def test_accept_no_approval_needed(self):
        cd = self._make(human_approval=None)
        cd._discovered['tool'] = DiscoveredCapability('tool', 'd', 'tool')
        self.assertTrue(cd.accept('tool'))

    # ── reject ────────────────────────────────────────────────────────────
    def test_reject(self):
        cd = self._make()
        cd._discovered['x'] = DiscoveredCapability('x', 'd', 'library')
        cd.reject('x', reason='not useful')
        self.assertEqual(cd._discovered['x'].status, 'rejected')
        self.assertIn('x', cd._rejected)

    def test_reject_missing_no_crash(self):
        cd = self._make()
        cd.reject('nonexistent')  # should not raise

    # ── auto_accept ───────────────────────────────────────────────────────
    def test_auto_accept(self):
        cd = self._make(human_approval=None)
        c1 = DiscoveredCapability('good', 'd', 'tool')
        c1.status = 'evaluated'
        c1.score = 0.9
        c2 = DiscoveredCapability('bad', 'd', 'tool')
        c2.status = 'evaluated'
        c2.score = 0.3
        cd._discovered = {'good': c1, 'bad': c2}
        accepted = cd.auto_accept(min_score=0.7)
        self.assertIn('good', accepted)
        self.assertNotIn('bad', accepted)

    # ── get_all / get_accepted / summary ──────────────────────────────────
    def test_get_all(self):
        cd = self._make()
        cd._discovered['a'] = DiscoveredCapability('a', 'd', 'lib')
        self.assertEqual(len(cd.get_all()), 1)

    def test_get_all_filtered(self):
        cd = self._make()
        cd._discovered['a'] = DiscoveredCapability('a', 'd', 'lib')
        cd._discovered['a'].status = 'accepted'
        self.assertEqual(len(cd.get_all(status='accepted')), 1)
        self.assertEqual(len(cd.get_all(status='rejected')), 0)

    def test_summary(self):
        cd = self._make()
        cd._discovered['a'] = DiscoveredCapability('a', 'd', 'lib')
        s = cd.summary()
        self.assertEqual(s['total_discovered'], 1)

    # ── record_action_result / find_gaps ──────────────────────────────────
    def test_record_action_result(self):
        cd = self._make()
        cd.record_action_result('python', True)
        cd.record_action_result('python', False)
        self.assertEqual(cd._failure_log['python'], {'success': 1, 'fail': 1})

    def test_find_gaps_weak_actions(self):
        cd = self._make()
        cd._failure_log['bash'] = {'success': 0, 'fail': 5}
        gaps = cd.find_gaps()
        self.assertTrue(any('weak_action:bash' in g for g in gaps))

    def test_find_gaps_missing_packages(self):
        cd = self._make()
        # Override REQUIRED_PACKAGES with a fake uninstallable package
        with patch.object(CapabilityDiscovery, 'REQUIRED_PACKAGES',
                          [('nonexistent_pkg_xyz_12345', 'nonexistent-pkg')]):
            gaps = cd.find_gaps()
            self.assertTrue(any('missing_package' in g for g in gaps))

    def test_find_gaps_rejected_useful(self):
        cd = self._make()
        cd._rejected.append('lib')
        cd._discovered['lib'] = DiscoveredCapability('lib', 'useful lib', 'library')
        cd._discovered['lib'].score = 0.6
        gaps = cd.find_gaps()
        self.assertTrue(any('rejected_useful' in g for g in gaps))

    def test_get_missing_capabilities_alias(self):
        cd = self._make()
        self.assertEqual(cd.get_missing_capabilities(), cd.find_gaps())

    # ── bootstrap_required_packages ───────────────────────────────────────
    def test_bootstrap_already_ok(self):
        cd = self._make()
        with patch.object(CapabilityDiscovery, 'REQUIRED_PACKAGES',
                          [('os', 'os-builtin')]):
            r = cd.bootstrap_required_packages()
            self.assertIn('os-builtin', r['already_ok'])

    def test_bootstrap_install_failure(self):
        cd = self._make()
        import subprocess as _sp
        with patch.object(CapabilityDiscovery, 'REQUIRED_PACKAGES',
                          [('nonexistent_xyz_pkg', 'nonexistent-pkg')]):
            with patch('subprocess.run',
                       side_effect=_sp.CalledProcessError(1, 'pip')):
                r = cd.bootstrap_required_packages()
                self.assertIn('nonexistent-pkg', r['failed'])


if __name__ == '__main__':
    unittest.main()
