from __future__ import annotations

import json

from click.testing import CliRunner

from neo.cli.main import cli
from neo.config import Settings


def test_user_config_loads_before_local_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NEO_LLM_MODEL", raising=False)
    monkeypatch.delenv("NEO_AGENT_NAME", raising=False)
    user_env = tmp_path / "user.env"
    local_env = tmp_path / "local.env"
    user_env.write_text("NEO_LLM_MODEL=user-model\nNEO_AGENT_NAME=user-agent\n")
    local_env.write_text("NEO_LLM_MODEL=local-model\n")

    settings = Settings(_env_file=(user_env, local_env))

    assert settings.llm_model == "local-model"
    assert settings.agent_name == "user-agent"


def test_mcp_config_agent_name_uses_command_arg() -> None:
    result = CliRunner().invoke(cli, ["mcp-config", "--agent-name", "hermes"])

    assert result.exit_code == 0
    config = json.loads(result.output)
    server = config["mcpServers"]["neo"]
    assert server["args"] == ["serve", "--agent-name", "hermes"]
    assert "env" not in server
