"""测试公共初始化。

把 backend 目录加入导入路径，便于直接从仓库根目录运行 pytest。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
