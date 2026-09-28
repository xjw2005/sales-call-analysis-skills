"""与小程序 utils/constants.js、Python 分析流水线保持一致"""

FIRST_STAGE = "首访破冰"
DAILY_STAGES = ["需求确认", "方案讲解", "异议处理", "促成签约", "交付培训", "日常维护", "复活挽回", "确定联合推广"]
STAGES = [FIRST_STAGE, *DAILY_STAGES]

FIRST_MODULES = ["explicit-needs", "implicit-needs", "concerns", "effectiveness", "quotes", "store-profile", "next-action"]
DAILY_MODULES = ["explicit-needs", "concerns", "store-profile", "next-action"]

PROFILE_SECTIONS = [
    ("one_line", "一句话画像"),
    ("basic", "门店基本信息"),
    ("categories_brands", "主营品类与品牌"),
    ("business_model", "经营模式"),
    ("selection_motion", "选品偏好"),
    ("price_profit", "利润偏好"),
    ("cooperation_preferences", "合作偏好与排斥项"),
]
PROFILE_LABELS = dict(PROFILE_SECTIONS)
PROFILE_STATES = ["稳定档案", "当前状态", "未确认"]

STATUSES = [
    "uploading", "cost_pending", "asr_running", "role_classifying", "analyzing", "reviewing",
    "done", "partial_manual", "invalid_short", "invalid_content", "failed",
]
PROCESSING = ["uploading", "asr_running", "role_classifying", "analyzing", "reviewing"]
ANALYZED = ["done", "partial_manual"]


def visit_mode(stage: str) -> str:
    return "first" if stage == FIRST_STAGE else "daily"


def modules_for(stage: str) -> list[str]:
    return FIRST_MODULES if stage == FIRST_STAGE else DAILY_MODULES
