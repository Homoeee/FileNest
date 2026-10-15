"""构建脚本应接受开发者自己的 Python 环境，而非固定机器路径。"""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


BUILD_SCRIPT = Path(__file__).resolve().parents[1] / 'packaging' / 'build.py'
SPEC = importlib.util.spec_from_file_location('filenest_build', BUILD_SCRIPT)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class BuildEnvironmentTests(unittest.TestCase):
    def test_accepts_another_64_bit_python_environment(self):
        with patch.object(build.sys, 'platform', 'win32'), \
             patch.object(build.sys, 'version_info', (3, 11)), \
             patch.object(build.sys, 'executable', r'C:\OtherDeveloper\Python311\python.exe'), \
             patch.object(build.struct, 'calcsize', return_value=8), \
             patch.object(build.importlib.metadata, 'version', return_value='6.22.3'):
            self.assertIsNone(build.check_environment())

    def test_rejects_unsupported_python_architecture(self):
        with patch.object(build.sys, 'platform', 'win32'), \
             patch.object(build.sys, 'version_info', (3, 11)), \
             patch.object(build.struct, 'calcsize', return_value=4):
            with self.assertRaisesRegex(SystemExit, '64 位'):
                build.check_environment()


if __name__ == '__main__':
    unittest.main()
