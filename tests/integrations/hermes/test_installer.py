import json
from pathlib import Path

from neo.integrations.hermes.installer import install_hermes_plugin


def test_installer_writes_plugin_and_config(tmp_path: Path):
    result = install_hermes_plugin(tmp_path, agent_name="atlas", set_active=False)

    plugin_dir = tmp_path / "plugins" / "neo"
    assert (plugin_dir / "__init__.py").exists()
    assert (plugin_dir / "plugin.yaml").exists()
    assert (plugin_dir / "README.md").exists()
    assert (tmp_path / "neo.json").exists()
    assert result["plugin_dir"] == str(plugin_dir)
    assert json.loads((tmp_path / "neo.json").read_text())["agent_name"] == "atlas"


def test_installer_does_not_overwrite_existing_config_without_force(tmp_path: Path):
    (tmp_path / "neo.json").write_text('{"agent_name":"existing","top_k":9}')

    install_hermes_plugin(tmp_path, agent_name="atlas", set_active=False)

    config = json.loads((tmp_path / "neo.json").read_text())
    assert config["agent_name"] == "existing"
    assert config["top_k"] == 9
