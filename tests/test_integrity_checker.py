"""Tests for safety/integrity_checker.py — IntegrityChecker (file hashes + supply chain)."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock


class IntegrityCheckerFileTests(unittest.TestCase):
    """Проверка целостности файлов через хеши."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_checker(self, monitoring=None):
        from safety.integrity_checker import IntegrityChecker
        return IntegrityChecker(working_dir=self.tmpdir, monitoring=monitoring)

    def _write_file(self, name, content):
        path = os.path.join(self.tmpdir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_verify_unknown_file_returns_true(self):
        ic = self._make_checker()
        path = self._write_file('unknown.json', '{}')
        self.assertTrue(ic.verify_file(path))

    def test_record_then_verify_unchanged(self):
        ic = self._make_checker()
        path = self._write_file('data.json', '{"key": "value"}')
        ic.record(path)
        self.assertTrue(ic.verify_file(path))

    def test_record_then_verify_changed(self):
        mon = MagicMock()
        ic = self._make_checker(monitoring=mon)
        path = self._write_file('data.json', '{"key": "value"}')
        ic.record(path)
        # Подменяем файл
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{"key": "HACKED"}')
        self.assertFalse(ic.verify_file(path))
        mon.warning.assert_called()
        self.assertIn("НЕ совпадает", mon.warning.call_args[0][0])

    def test_record_multiple_files(self):
        ic = self._make_checker()
        p1 = self._write_file('a.json', '1')
        p2 = self._write_file('b.json', '2')
        ic.record(p1)
        ic.record(p2)
        self.assertTrue(ic.verify_file(p1))
        self.assertTrue(ic.verify_file(p2))

    def test_re_record_updates_hash(self):
        ic = self._make_checker()
        path = self._write_file('data.json', 'v1')
        ic.record(path)
        # Изменяем и перезаписываем эталон
        with open(path, 'w', encoding='utf-8') as f:
            f.write('v2')
        ic.record(path)
        self.assertTrue(ic.verify_file(path))

    def test_verify_nonexistent_file_returns_false(self):
        mon = MagicMock()
        ic = self._make_checker(monitoring=mon)
        path = self._write_file('temp.json', 'x')
        ic.record(path)
        os.remove(path)
        self.assertFalse(ic.verify_file(path))


class IntegrityCheckerManifestTests(unittest.TestCase):
    """Персистентность manifest."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_checker(self):
        from safety.integrity_checker import IntegrityChecker
        return IntegrityChecker(working_dir=self.tmpdir)

    def _write_file(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_manifest_saved_to_disk(self):
        ic = self._make_checker()
        path = self._write_file('test.json', '{}')
        ic.record(path)
        manifest_path = os.path.join(self.tmpdir, '.integrity_manifest.json')
        self.assertTrue(os.path.exists(manifest_path))
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.assertIn('test.json', data)

    def test_manifest_survives_reload(self):
        from safety.integrity_checker import IntegrityChecker
        path = self._write_file('data.json', 'content')
        ic1 = IntegrityChecker(working_dir=self.tmpdir)
        ic1.record(path)
        # Создаём новый экземпляр (имитация перезапуска)
        ic2 = IntegrityChecker(working_dir=self.tmpdir)
        self.assertTrue(ic2.verify_file(path))

    def test_corrupt_manifest_handled(self):
        manifest_path = os.path.join(self.tmpdir, '.integrity_manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as f:
            f.write('NOT JSON')
        from safety.integrity_checker import IntegrityChecker
        ic = IntegrityChecker(working_dir=self.tmpdir)
        self.assertEqual(ic._hashes, {})

    def test_empty_manifest_dir_created(self):
        import shutil
        subdir = os.path.join(self.tmpdir, 'sub', 'deep')
        os.makedirs(subdir)
        from safety.integrity_checker import IntegrityChecker
        ic = IntegrityChecker(working_dir=subdir)
        path = os.path.join(subdir, 'f.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('x')
        ic.record(path)
        manifest = os.path.join(subdir, '.integrity_manifest.json')
        self.assertTrue(os.path.exists(manifest))


class IntegrityCheckerPackageTests(unittest.TestCase):
    """Supply chain: проверка пакетов по белому списку."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_checker(self, monitoring=None):
        from safety.integrity_checker import IntegrityChecker
        return IntegrityChecker(working_dir=self.tmpdir, monitoring=monitoring)

    def _write_requirements(self, lines):
        path = os.path.join(self.tmpdir, 'requirements.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return path

    def test_no_requirements_file_blocks_all(self):
        """Без requirements.txt — fail-closed: все пакеты заблокированы."""
        ic = self._make_checker()
        packages = [('requests', 'requests'), ('numpy', 'numpy')]
        allowed, blocked = ic.validate_packages(packages)
        self.assertEqual(len(allowed), 0)
        self.assertEqual(len(blocked), 2)

    def test_in_whitelist_allowed(self):
        self._write_requirements(['requests>=2.0', 'numpy==1.25'])
        ic = self._make_checker()
        packages = [('requests', 'requests'), ('numpy', 'numpy')]
        allowed, blocked = ic.validate_packages(packages)
        self.assertEqual(allowed, ['requests', 'numpy'])
        self.assertEqual(blocked, [])

    def test_not_in_whitelist_blocked(self):
        mon = MagicMock()
        self._write_requirements(['requests>=2.0'])
        ic = self._make_checker(monitoring=mon)
        packages = [('requests', 'requests'), ('evil_pkg', 'evil-pkg')]
        allowed, blocked = ic.validate_packages(packages)
        self.assertEqual(allowed, ['requests'])
        self.assertEqual(blocked, ['evil-pkg'])
        mon.warning.assert_called()

    def test_dash_underscore_normalization(self):
        self._write_requirements(['deep-translator', 'google-api-python-client'])
        ic = self._make_checker()
        packages = [
            ('deep_translator', 'deep-translator'),
            ('googleapiclient', 'google-api-python-client'),
        ]
        allowed, blocked = ic.validate_packages(packages)
        self.assertEqual(len(allowed), 2)
        self.assertEqual(len(blocked), 0)

    def test_custom_requirements_path(self):
        custom = os.path.join(self.tmpdir, 'custom_req.txt')
        with open(custom, 'w', encoding='utf-8') as f:
            f.write('psutil\n')
        ic = self._make_checker()
        packages = [('psutil', 'psutil'), ('numpy', 'numpy')]
        allowed, blocked = ic.validate_packages(packages, requirements_path=custom)
        self.assertEqual(allowed, ['psutil'])
        self.assertEqual(blocked, ['numpy'])

    def test_comments_and_empty_lines_ignored(self):
        self._write_requirements([
            '# Comment',
            '',
            'requests>=2.0',
            '-r other.txt',
            '  ',
            'numpy',
        ])
        ic = self._make_checker()
        packages = [('requests', 'requests'), ('numpy', 'numpy')]
        allowed, _blocked = ic.validate_packages(packages)
        self.assertEqual(len(allowed), 2)

    def test_version_specifiers_stripped(self):
        self._write_requirements([
            'package1>=1.0',
            'package2<=2.0',
            'package3==3.0',
            'package4~=4.0',
            'package5!=5.0',
            'package6[extra]',
        ])
        ic = self._make_checker()
        packages = [
            ('package1', 'package1'),
            ('package2', 'package2'),
            ('package3', 'package3'),
            ('package4', 'package4'),
            ('package5', 'package5'),
            ('package6', 'package6'),
        ]
        allowed, blocked = ic.validate_packages(packages)
        self.assertEqual(len(allowed), 6)
        self.assertEqual(len(blocked), 0)


class IntegrityCheckerHashTests(unittest.TestCase):
    """Внутренние хеш-утилиты."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_checker(self):
        from safety.integrity_checker import IntegrityChecker
        return IntegrityChecker(working_dir=self.tmpdir)

    def test_hash_file_returns_64_hex(self):
        ic = self._make_checker()
        path = os.path.join(self.tmpdir, 'f.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('hello')
        h = ic._hash_file(path)
        self.assertIsNotNone(h)
        assert h is not None
        self.assertEqual(len(h), 64)

    def test_hash_file_nonexistent_returns_none(self):
        ic = self._make_checker()
        self.assertIsNone(ic._hash_file('/nonexistent/path'))

    def test_hash_file_deterministic(self):
        ic = self._make_checker()
        path = os.path.join(self.tmpdir, 'f.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('test content')
        h1 = ic._hash_file(path)
        h2 = ic._hash_file(path)
        self.assertEqual(h1, h2)

    def test_hash_file_different_content_different_hash(self):
        ic = self._make_checker()
        p1 = os.path.join(self.tmpdir, 'a.txt')
        p2 = os.path.join(self.tmpdir, 'b.txt')
        with open(p1, 'w', encoding='utf-8') as f:
            f.write('content A')
        with open(p2, 'w', encoding='utf-8') as f:
            f.write('content B')
        self.assertNotEqual(ic._hash_file(p1), ic._hash_file(p2))

    def test_record_nonexistent_file_is_noop(self):
        ic = self._make_checker()
        ic.record('/nonexistent/file.json')
        self.assertEqual(ic._hashes, {})

    def test_large_file_hashing(self):
        ic = self._make_checker()
        path = os.path.join(self.tmpdir, 'big.bin')
        with open(path, 'wb') as f:
            f.write(b'x' * 200_000)
        h = ic._hash_file(path)
        self.assertIsNotNone(h)


if __name__ == '__main__':
    unittest.main()
