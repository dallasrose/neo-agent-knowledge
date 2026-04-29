from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Literal

AutoIngestMode = Literal[False, "explicit-only", "all"]
RecallMode = Literal["signals-first", "compact", "off"]
ScopeMode = Literal["self", "network"]


@dataclass(slots=True)
class HermesNeoConfig:
    """Profile-scoped Neo configuration for the Hermes memory provider."""

    agent_name: str = "default"
    top_k: int = 6
    hop_depth: int = 2
    token_budget: int = 1200
    signal_token_budget: int = 350
    max_signals: int = 5
    min_confidence: float = 0.45
    include_sparks: bool = True
    auto_ingest: AutoIngestMode = "explicit-only"
    recall_mode: RecallMode = "signals-first"
    hint_threshold: float = 0.48
    signal_threshold: float = 0.55
    expand_threshold: float = 0.78
    scope: ScopeMode = "self"

    @classmethod
    def load(cls, hermes_home: str | Path | None) -> "HermesNeoConfig":
        if not hermes_home:
            config = cls()
            config.validate()
            return config
        path = Path(hermes_home).expanduser() / "neo.json"
        if not path.exists():
            config = cls()
            config.validate()
            return config
        raw = json.loads(path.read_text())
        allowed = {field.name for field in fields(cls)}
        values = {key: value for key, value in raw.items() if key in allowed}
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if not self.agent_name.strip():
            raise ValueError("agent_name must not be empty")
        if self.top_k < 1:
            raise ValueError("top_k must be >= 1")
        if self.hop_depth < 0:
            raise ValueError("hop_depth must be >= 0")
        if self.token_budget < 1:
            raise ValueError("token_budget must be >= 1")
        if self.signal_token_budget < 1:
            raise ValueError("signal_token_budget must be >= 1")
        if self.signal_token_budget > self.token_budget:
            self.signal_token_budget = self.token_budget
        if self.max_signals < 1:
            raise ValueError("max_signals must be >= 1")
        for name in ("min_confidence", "hint_threshold", "signal_threshold", "expand_threshold"):
            value = float(getattr(self, name))
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
            setattr(self, name, value)
        if self.recall_mode not in {"signals-first", "compact", "off"}:
            raise ValueError("recall_mode must be one of: signals-first, compact, off")
        if self.auto_ingest not in {False, "explicit-only", "all"}:
            raise ValueError("auto_ingest must be false, explicit-only, or all")
        if self.scope not in {"self", "network"}:
            raise ValueError("scope must be self or network")

    def save(self, hermes_home: str | Path) -> Path:
        self.validate()
        path = Path(hermes_home).expanduser() / "neo.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return path
