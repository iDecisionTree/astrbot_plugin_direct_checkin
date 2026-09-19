"""图表及报表共用语义颜色与中文字体。"""

from pathlib import Path

NAVY = "#17324D"
BLUE = "#2878D0"
TEAL = "#129C96"
AMBER = "#CF8825"
MUTED = "#667D91"
PALE = "#F2F7FB"
ZERO = "#A8BACB"
FONT_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"
)
STATUS_LABELS = {
    "PENDING": "处理中",
    "VALID_COUNTED": "有效计次",
    "VALID_EXTRA": "额外材料",
    "REJECTED_AI": "审核未通过",
    "REJECTED_DUPLICATE": "完全重复",
    "REJECTED_FILE": "文件不可用",
    "AI_ERROR": "审核服务异常",
    "PROCESSING_ERROR": "处理异常",
}


def status_label(status):
    return STATUS_LABELS.get(status, status)
