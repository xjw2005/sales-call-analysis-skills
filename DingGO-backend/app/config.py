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
