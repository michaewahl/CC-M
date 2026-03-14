from pydantic_settings import BaseSettings, SettingsConfigDict


class CCMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CCM_", env_file=".env", extra="ignore")

    port: int = 8082
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"

    # Model tier mapping
    model_simple: str = "claude-haiku-4-5-20251001"
    model_medium: str = "claude-sonnet-4-6"
    model_complex: str = "claude-opus-4-6"

    # Classification score thresholds
    threshold_medium: float = 1.5
    threshold_complex: float = 3.5

    # Force a specific model (bypasses classifier entirely)
    force_model: str = ""

    # Cost tracking
    store_path: str = "~/.cc-m/cost.db"

    # Logging
    log_classifications: bool = True

    # Timeout for Anthropic API calls (seconds)
    request_timeout: float = 120.0

    # Shadow calibration mode
    calibration_enabled: bool = False
    calibration_sample_rate: float = 0.2   # shadow 1 in 5 prompts
    calibration_max_prompts: int = 50      # stop after N shadows


settings = CCMSettings()
