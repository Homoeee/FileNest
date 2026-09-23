"""从项目根目录生成 Windows 便携版；不安装依赖、不覆盖已有构建。"""

import importlib.metadata
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_PYTHON = Path(r'D:\anaconda\Anaconda3\envs\agent-dev\python.exe')


def main():
    if Path(sys.executable).resolve() != EXPECTED_PYTHON.resolve():
        raise SystemExit(f'请用项目指定解释器运行：{EXPECTED_PYTHON}')
    if sys.platform != 'win32':
        raise SystemExit('当前构建脚本仅生成 Windows 版本')
    try:
        version = importlib.metadata.version('pyinstaller')
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit('缺少 PyInstaller；请先按项目规范取得安装批准，不会自动安装')
    if not version.startswith('6.'):
        raise SystemExit(f'当前 PyInstaller {version} 未验证；请使用 6.x')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    release_name = f'FileNest-Windows-x64-{stamp}'
    release_dir = ROOT / 'dist' / release_name
    work_dir = ROOT / 'build' / release_name
    archive = ROOT / 'dist' / f'{release_name}.zip'
    if release_dir.exists() or work_dir.exists() or archive.exists():
        raise SystemExit('同名构建已存在；请稍后重新运行，不会覆盖旧版本')
    command = [sys.executable, '-m', 'PyInstaller', '--onedir', '--console',
               '--name', 'FileNest', '--distpath', str(release_dir),
               '--workpath', str(work_dir), '--specpath', str(work_dir),
               '--add-data', f'{ROOT / "web"};web',
               '--exclude-module', 'ui.main_window', '--exclude-module', 'tkinter',
               str(ROOT / 'main.py')]
    subprocess.run(command, cwd=ROOT, check=True)
    app_dir = release_dir / 'FileNest'
    if not (app_dir / 'FileNest.exe').is_file():
        raise RuntimeError('打包器未生成 FileNest.exe')
    shutil.copy2(ROOT / 'packaging' / 'FileNest-Web.cmd', app_dir / 'FileNest-Web.cmd')
    shutil.copy2(ROOT / 'packaging' / '使用说明.txt', app_dir / '使用说明.txt')
    shutil.make_archive(str(archive.with_suffix('')), 'zip', root_dir=release_dir,
                        base_dir='FileNest')
    print(f'便携包：{archive}')


if __name__ == '__main__':
    main()
