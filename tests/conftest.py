import sys
from pathlib import Path

# 让插件以包的形式导入，保持与 AstrBot 运行时一致的相对导入语义。
ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))
