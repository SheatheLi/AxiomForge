"""直接运行公开 Issue 机制验证，不依赖 AxiomForge 安装或第三方包。"""
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    raise SystemExit(subprocess.call([sys.executable, str(root / 'scripts' / 'verify_external_issues.py')], cwd=root))
