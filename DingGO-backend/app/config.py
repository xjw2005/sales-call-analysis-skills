from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """所有配置都从环境变量 / .env 读取，见 .env.example。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/dev.db"
    jwt_secret: str = "change-me"
    jwt_expire_days: int = 30

    # 小程序 AppID / AppSecret；AppSecret 为空时只能用开发登录
    wx_appid: str = "wxdf1c03b7933a33fa"
    wx_secret: str = ""
    # 开发登录：不调微信接口，用 code 直接当账号（仅开发调试用，上线必须关闭）
    dev_login: bool = True

    # 录音文件存放目录与对外访问地址（生成带时效的下载链接）
    storage_dir: str = "./data/uploads"
    public_base_url: str = "http://localhost:8000"
    file_url_expire_seconds: int = 3600

    # 大模型（OpenAI 兼容接口，如火山方舟）；密钥只放服务器 .env
    llm_api_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-pro"
    llm_temperature: float = 0.1
    role_llm_model: str = ""  # 角色标注和有效性判断用的模型，空则同 llm_model（可填更便宜的 flash 模型）
    # 分析复核：off | flagged | all；fallback-primary 表示复核不收敛时保留首轮结果
    review_mode: str = "flagged"
    review_policy: str = "fallback-primary"
    # 知识库目录（含 manifest 之外的 .md 条目）；空表示不注入知识
    knowledge_root: str = "./knowledge/a2"
    knowledge_enabled: bool = True
    # 火山 LAS 语音转写；密钥只放服务器 .env
    las_api_key: str = ""
    las_region: str = "cn-beijing"
    las_operator_id: str = "las_asr_seed-2-0-lite"
    las_operator_version: str = "v1"
    lasutil_path: str = ""
    las_poll_seconds: int = 10
    las_timeout_seconds: int = 3600
    # 后台处理线程数与每人每日分析次数上限
    worker_threads: int = 2
    daily_visit_limit: int = 30
    daily_chat_limit: int = 200  # 每人每天最多提问次数

    # 识别费用估算：每小时录音多少元
    asr_price_per_hour: float = 1.26
    valid_min_seconds: int = 120
    stale_days: int = 14
    timezone: str = "Asia/Shanghai"

    # 管理接口令牌（导入分析结果用）；为空时管理接口关闭
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
