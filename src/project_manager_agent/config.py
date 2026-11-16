"""Configuration from environment variables (and an optional `.env` file).

Every setting has a safe default, so offline commands work without credentials.
Only model-backed commands need OPENAI_API_KEY.
"""

from __future__ import annotations

import getpass
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "gpt-5.4-mini"
RESERVED_APPROVER_NAMES = {"agent", "llm", "model", "assistant", "system", "ai", "bot", "pm-agent"}


class MissingCredentialsError(RuntimeError):
    """Raised when a model-backed command runs without OPENAI_API_KEY."""


def find_project_root() -> Path:
    override = os.environ.get("PM_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve()
    for candidate in [*here.parents, Path.cwd()]:
        if (candidate / "knowledge-base").is_dir() and (candidate / "agent").is_dir() and (candidate / "data").is_dir():
            return candidate
    raise RuntimeError("Could not find the project root (folder with agent/, data/ and knowledge-base/). "
                       "Run commands from the repository folder or set PM_PROJECT_ROOT.")


def _load_dotenv(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - declared dependency
        return
    load_dotenv(env_file, override=False)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


@dataclass
class Settings:
    project_root: Path
    openai_api_key: str | None = field(default=None, repr=False)
    openai_model: str = DEFAULT_MODEL
    openai_reasoning_effort: str | None = "low"
    max_iterations: int = 12
    max_tool_calls: int = 30
    max_tool_output_chars: int = 12000
    request_timeout_seconds: int = 120
    max_submission_retries: int = 2
    state_dir_override: Path | None = None
    approver: str = ""
    scenario: str = "launch-delay"

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def agent_dir(self) -> Path:
        return self.project_root / "agent"

    @property
    def workflows_dir(self) -> Path:
        return self.agent_dir / "workflows"

    @property
    def knowledge_dir(self) -> Path:
        return self.project_root / "knowledge-base"

    @property
    def state_dir(self) -> Path:
        return self.state_dir_override or (self.project_root / "state")

    @property
    def db_path(self) -> Path:
        return self.state_dir / "northstar_pm.db"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.project_root / "reports"

    @property
    def policy_path(self) -> Path:
        return self.project_root / "config" / "policy.json"

    @property
    def has_openai_key(self) -> bool:
        return bool(self.openai_api_key)

    def require_openai_key(self) -> str:
        if not self.openai_api_key:
            raise MissingCredentialsError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key, or re-run the "
                "command with --offline to use the deterministic rules engine instead of the model.")
        return self.openai_api_key

    def load_policy(self) -> dict[str, Any]:
        return json.loads(self.policy_path.read_text(encoding="utf-8"))


def load_settings(project_root: Path | None = None, *, state_dir: Path | None = None) -> Settings:
    root = project_root or find_project_root()
    _load_dotenv(root)
    effort = os.environ.get("OPENAI_REASONING_EFFORT", "low").strip() or None
    env_state = os.environ.get("PM_STATE_DIR", "").strip()
    resolved_state = state_dir or ((root / env_state) if env_state and not Path(env_state).is_absolute()
                                   else (Path(env_state) if env_state else None))
    try:
        default_user = getpass.getuser()
    except Exception:  # pragma: no cover
        default_user = "local-user"
    return Settings(
        project_root=root,
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip() or None,
        openai_model=os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_MODEL,
        openai_reasoning_effort=effort,
        max_iterations=_int_env("PM_MAX_ITERATIONS", 12),
        max_tool_calls=_int_env("PM_MAX_TOOL_CALLS", 30),
        max_tool_output_chars=_int_env("PM_MAX_TOOL_OUTPUT_CHARS", 12000),
        request_timeout_seconds=_int_env("PM_REQUEST_TIMEOUT_SECONDS", 120),
        state_dir_override=resolved_state,
        approver=os.environ.get("PM_APPROVER", "").strip() or default_user,
        scenario=os.environ.get("PM_SCENARIO", "").strip() or "launch-delay",
    )
