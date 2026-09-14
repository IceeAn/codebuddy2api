"""用独立解释器验证发现测试和直接导入均先隔离开发数据。"""

import tests  # 在生产模块导入前隔离测试数据目录。
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


class TestInitializationTests(unittest.TestCase):
    def test_imports_isolate_data_before_config_and_leave_development_database_untouched(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'codebuddy2api.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('PRAGMA user_version=999')
            before = database.read_bytes()
            for mode in ('discover', 'module', 'helpers', 'unset'):
                code = '''
import builtins, json, os, sys, unittest
original = sys.argv[2]
original_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'config' and level == 0:
        assert os.environ.get('CODEBUDDY_DATA_DIR') and os.environ['CODEBUDDY_DATA_DIR'] != original, 'config 导入前未隔离数据目录'
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded_import
if sys.argv[1] == 'discover':
    suite = unittest.defaultTestLoader.discover('tests', pattern='test_responses_routes.py')
    assert not unittest.defaultTestLoader.errors, unittest.defaultTestLoader.errors
elif sys.argv[1] == 'module':
    import tests.test_responses_routes
else:
    import tests.helpers
import config
assert config.get_data_dir() != original
print(json.dumps({'directory': os.environ['CODEBUDDY_DATA_DIR']}))
'''
                env = {**os.environ, 'CODEBUDDY_DATA_DIR': directory, 'CODEBUDDY_LOG_LEVEL': 'CRITICAL'}
                if mode == 'unset':
                    env.pop('CODEBUDDY_DATA_DIR')
                result = subprocess.run([sys.executable, '-c', code, mode, directory], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=30)
                with self.subTest(mode=mode):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    isolated = json.loads(result.stdout.splitlines()[-1])['directory']
                    self.assertFalse(Path(isolated).exists(), '解释器退出后应清理临时目录')
                    self.assertEqual(database.read_bytes(), before)
                    self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), [database.name])
