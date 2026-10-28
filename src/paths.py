"""项目文件位置，开发环境使用 pip install -e . 安装。"""
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data"
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"
TIMEZONE = ZoneInfo("Asia/Shanghai")
