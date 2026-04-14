from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default Neo state lives in ~/.neo/ so it is consistent regardless of which
# agent host launches Neo or which directory neo serve is called from.
_DEFAULT_CONFIG_DIR = Path.home() / ".neo"
_DEFAULT_DB = _DEFAULT_CONFIG_DIR / "neo.db"
_DEFAULT_ENV = _DEFAULT_CONFIG_DIR / ".env"
_DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEO_",
        env_file=(_DEFAULT_ENV, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_connection_uri: str = Field(
        default=f"sqlite+aiosqlite:///{_DEFAULT_DB}",
        validation_alias=AliasChoices("NEO_DB_CONNECTION_URI", "NEO_DATABASE_URL"),
    )
    db_sql_debug: bool = False

    embedding_provider: str = "openai"
    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_fallback_enabled: bool = True

    # General LLM defaults used by spark generation, resolution, and discovery
    # unless a task-specific override below is set.
    llm_provider: str = "anthropic"
    llm_model: str = ""
    llm_api_key: str | None = None
    llm_base_url: str | None = None

    llm_spark_provider: str = ""
    llm_spark_model: str = "claude-haiku-4-5"
    llm_spark_api_key: str | None = None
    llm_spark_base_url: str | None = None  # Custom base URL (e.g. MiniMax Anthropic-compat endpoint)
    llm_relationship_provider: str = ""
    llm_relationship_model: str = ""
    llm_relationship_api_key: str | None = None
    llm_relationship_base_url: str | None = None
    llm_consolidation_provider: str = ""
    llm_consolidation_model: str = "claude-sonnet-4-20250514"
    llm_consolidation_api_key: str | None = None
    llm_consolidation_base_url: str | None = None

    consolidation_schedule: str = Field(default="0 */6 * * *")
    consolidation_node_threshold: int = 20
    consolidation_enabled: bool = True
    scheduler_poll_interval_seconds: float = 1.0

    # Contemplation: background loop that generates sparks for nodes that have none
    contemplation_enabled: bool = True
    contemplation_interval_minutes: int = 15  # how often to run
    contemplation_batch_size: int = 10        # nodes to process per cycle

    # Web search (for background spark resolution)
    search_provider: str = "tavily"
    search_api_key: str | None = None

    # Discovery: proactive content ingestion from configured sources + autonomous search
    # Enabled by default — does nothing until the agent has a specialty set.
    # To activate: call configure_agent (via MCP) with a research direction.
    discovery_enabled: bool = True
    discovery_interval_minutes: int = 60   # how often to poll sources
    discovery_batch_size: int = 5          # max new items to ingest per source per cycle
    discovery_lookback_days: int = 30      # how far back to look for new content

    # Optional: YouTube Data API key for higher-quality search results.
    # Without it, discovery falls back to web search (Exa/Tavily) scoped to youtube.com,
    # which works fine if NEO_SEARCH_API_KEY is set.
    # Get a free key at: console.cloud.google.com → YouTube Data API v3
    youtube_api_key: str | None = None

    # Resolution scheduler
    resolution_enabled: bool = False
    resolution_interval_minutes: int = 30
    resolution_batch_size: int = 3

    # LLM for resolution (defaults to spark LLM if not set)
    llm_resolution_provider: str = ""
    llm_resolution_model: str = ""
    llm_resolution_api_key: str | None = None
    llm_resolution_base_url: str | None = None

    agent_name: str = "default"
    log_level: str = "INFO"
    rest_host: str = "127.0.0.1"
    rest_port: int = 8420

    # MCP HTTP transport (for remote deployments — e.g. Claude Managed Agents)
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8421
    mcp_api_key: str | None = None  # If set, HTTP requests must include X-Neo-Api-Key header

    @field_validator("embedding_provider")
    @classmethod
    def validate_embedding_provider(cls, value: str) -> str:
        supported = {"openai", "mock"}
        if value not in supported:
            raise ValueError(f"embedding_provider must be one of {sorted(supported)}")
        return value

    def llm_provider_for(self, task: str) -> str:
        override = getattr(self, f"llm_{task}_provider", "") or ""
        return override or self.llm_provider

    def llm_model_for(self, task: str) -> str:
        override = getattr(self, f"llm_{task}_model", "") or ""
        return override or self.llm_model or self.llm_spark_model

    def llm_api_key_for(self, task: str) -> str | None:
        override = getattr(self, f"llm_{task}_api_key", None)
        if override:
            return override
        return self.llm_api_key or self.llm_spark_api_key

    def llm_base_url_for(self, task: str) -> str | None:
        override = getattr(self, f"llm_{task}_base_url", None)
        if override:
            return override
        return self.llm_base_url or self.llm_spark_base_url

    def llm_configured_for(self, task: str) -> bool:
        provider = self.llm_provider_for(task).strip().lower()
        model = self.llm_model_for(task)
        if not model:
            return False
        if self.llm_api_key_for(task):
            return True
        if provider in {"ollama", "lmstudio", "lm-studio"}:
            return True
        openai_local = {
            "openai",
            "openai-compatible",
            "openai_compatible",
            "vllm",
            "llama.cpp",
        }
        return provider in openai_local and bool(self.llm_base_url_for(task))

    @field_validator("rest_port")
    @classmethod
    def validate_rest_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("rest_port must be between 1 and 65535")
        return value


def get_config_dir() -> Path:
    return _DEFAULT_CONFIG_DIR


def get_config_env_path() -> Path:
    return _DEFAULT_ENV


def set_runtime_agent_name(agent_name: str | None) -> None:
    if agent_name:
        settings.agent_name = agent_name


def read_env_file(path: Path | None = None) -> dict[str, str]:
    env_path = path or _DEFAULT_ENV
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_file(values: dict[str, str], path: Path | None = None) -> None:
    env_path = path or _DEFAULT_ENV
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Neo user-level config. Do not commit or share."]
    for key in sorted(k for k, v in values.items() if v is not None and str(v) != ""):
        lines.append(f"{key}={values[key]}")
    env_path.write_text("\n".join(lines) + "\n")
    env_path.chmod(0o600)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
