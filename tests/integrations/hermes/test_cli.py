from click.testing import CliRunner

from neo.cli.main import cli


def test_neo_hermes_install_cli_writes_plugin(tmp_path):
    runner = CliRunner()

    result = runner.invoke(
        cli,
        ["hermes", "install", "--hermes-home", str(tmp_path), "--agent-name", "atlas", "--force"],
    )

    assert result.exit_code == 0, result.output
    assert "Installed Neo Hermes plugin" in result.output
    assert (tmp_path / "plugins" / "neo" / "__init__.py").exists()
    assert (tmp_path / "neo.json").exists()
