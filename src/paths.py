"""项目文件位置，开发环境使用 pip install -e . 安装。"""
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data"
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"
#: 上游导出的本地数据目录，不进版本库（见 .gitignore）
LOCAL_DATA_ROOT = PROJECT_ROOT / ".local-data"
TIMEZONE = ZoneInfo("Asia/Shanghai")
