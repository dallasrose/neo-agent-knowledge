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


def test_setup_configures_neo_without_agent_identity(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / ".neo.env"
    monkeypatch.setattr("neo.cli.main.get_config_env_path", lambda: config_path)

    result = CliRunner().invoke(
        cli,
        [
            "setup",
            "--provider",
            "ollama",
            "--model",
            "llama3.2",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0
    content = config_path.read_text()
    assert "NEO_LLM_PROVIDER=ollama" in content
    assert "NEO_LLM_MODEL=llama3.2" in content
    assert "NEO_LLM_BASE_URL=http://127.0.0.1:11434/v1" in content
    assert "NEO_RESOLUTION_ENABLED=true" in content
    assert "NEO_RESOLUTION_INTERVAL_MINUTES=30" in content
    assert "NEO_RESOLUTION_BATCH_SIZE=3" in content
    assert "NEO_AGENT_NAME" not in content
    assert "No agent node was created" in result.output


def test_setup_disables_resolution_without_llm(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / ".neo.env"
    monkeypatch.setattr("neo.cli.main.get_config_env_path", lambda: config_path)

    result = CliRunner().invoke(
        cli,
        [
            "setup",
            "--provider",
            "none",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0
    content = config_path.read_text()
    assert "NEO_LLM_PROVIDER" not in content
    assert "NEO_RESOLUTION_ENABLED=false" in content


def test_default_cli_launches_visualizer(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("neo.cli.main._serve_rest", lambda host, port, agent_name: calls.append((host, port, agent_name)))

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert calls == [("127.0.0.1", 8420, None)]
