from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Load local configuration without depending on the current directory."""

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'studyagent.db').as_posix()}"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    data_dir: Path = BACKEND_DIR / "data"
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    max_text_chars: int = Field(default=2_000_000, ge=1)
    max_chunks: int = Field(default=5000, ge=1)
    chunk_size: int = Field(default=800, ge=1)
    chunk_overlap: int = Field(default=150, ge=0)
    paragraph_aware_chunks: bool = False
    chunk_max_utf8_bytes: int | None = Field(default=None, ge=4)
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    embedding_timeout_seconds: float = Field(default=30, gt=0)
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_min_score: float = Field(default=0.3, ge=-1, le=1)
    rag_max_context_chars: int = Field(default=16000, ge=1)
    enable_reranker: bool = False
    reranker_base_url: str = ""
    reranker_model: str = ""
    reranker_api_key: SecretStr = SecretStr("")
    reranker_candidate_k: int = Field(default=20, ge=20, le=100)
    reranker_timeout_seconds: float = Field(default=15, gt=0, le=120)
    reranker_max_input_chars: int = Field(default=100000, ge=1, le=500000)
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=60, gt=0)
    llm_max_tokens: int = Field(default=2048, ge=1, le=16384)
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_answer_chars: int = Field(default=32768, ge=1)
    agent_max_rounds: int = Field(default=6, ge=1, le=20)
    agent_max_tool_calls: int = Field(default=8, ge=1, le=32)
    agent_timeout_seconds: float = Field(default=120, gt=0, le=600)
    agent_max_context_chars: int = Field(default=100000, ge=1000, le=500000)
    memory_window_turns: int = Field(default=10, ge=1, le=50)
    memory_max_context_chars: int = Field(default=24000, ge=1000, le=100000)
    memory_summary_max_chars: int = Field(default=4000, ge=100, le=12000)
    memory_summary_input_chars: int = Field(default=24000, ge=1000, le=100000)
    conversation_max_prompt_chars: int = Field(default=100000, ge=4000, le=500000)
    conversation_max_prompt_tokens: int = Field(default=64000, ge=4000, le=200000)
    conversation_timeout_seconds: float = Field(default=180, gt=0, le=600)
    # Empty by default. SecretStr keeps optional proxy credentials out of repr/logs.
    model_http_proxy: SecretStr = SecretStr("")
    # Opt-in test mode is inert without an explicitly authorized sealed ledger and stage scope.
    live_test_mode: bool = False
    web_demo_enabled: bool = False
    web_demo_allow_writes: bool = False
    web_demo_allow_session_management: bool = False
    personal_learning: bool = False
    personal_alert_thresholds: list[int] = [30,50]
    web_demo_round_id: str = ""
    live_test_ledger: Path = BACKEND_DIR / "test-results" / "bailian-first-live" / "budget.sqlite"
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    @model_validator(mode="after")
    def validate_chunking(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        self.data_dir = self.data_dir.resolve()
        return self
