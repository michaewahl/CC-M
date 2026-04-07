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

    # Spend enforcement — hard daily budget caps (0 = disabled)
    budget_user_daily_usd: float = 0.0   # per-user daily cap
    budget_team_daily_usd: float = 0.0   # per-team daily cap

    # Tool-use interception
    tool_result_downgrade: bool = True  # route tool_result follow-ups to cheapest model
    tool_log_calls: bool = True         # log tool calls detected in SSE response streams

    # Swarm governance — detect and control sub-agent spawning
    # action: "log" (default), "cap" (limit max_tokens), "block" (require approval header)
    swarm_action: str = "log"
    swarm_tool_names: str = "agent,computer_use"  # comma-separated tool names to watch
    swarm_token_cap: int = 4096                   # max_tokens cap when action=cap
    swarm_require_header: str = "x-ccm-swarm-approved"  # header required when action=block

    # Logging
    log_classifications: bool = True

    # Timeout for Anthropic API calls (seconds)
    request_timeout: float = 120.0

    # Governance & visibility
    governance_enabled: bool = True
    admin_token: str = ""  # required for /stats, /calibration, /usage endpoints

    # Enterprise license
    license_key: str = ""  # passed to enterprise plugin for validation

    # Skill Pruner — tier-aware tool stripping
    pruner_enabled: bool = True          # set False to disable pruning entirely
    # Extra tool names (comma-separated) to strip on ALL non-COMPLEX tiers
    pruner_extra_blocked: str = ""

    # Shadow calibration mode
    calibration_enabled: bool = False
    calibration_sample_rate: float = 0.2   # shadow 1 in 5 prompts
    calibration_max_prompts: int = 50      # stop after N shadows


settings = CCMSettings()
