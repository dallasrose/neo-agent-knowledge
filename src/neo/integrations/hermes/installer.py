from __future__ import annotations

import json
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

from neo.integrations.hermes.config import HermesNeoConfig

_TEMPLATE_PACKAGE = "neo.integrations.hermes.plugin_template"
_TEMPLATE_FILES = ["__init__.py", "plugin.yaml", "README.md", "cli.py"]


def install_hermes_plugin(
    hermes_home: str | Path,
    *,
    agent_name: str = "default",
    set_active: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Install Neo's thin Hermes memory plugin shim into a Hermes home."""

    home = Path(hermes_home).expanduser()
    plugin_dir = home / "plugins" / "neo"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    template_root = files(_TEMPLATE_PACKAGE)
    for filename in _TEMPLATE_FILES:
        destination = plugin_dir / filename
        if destination.exists() and not force:
            continue
        destination.write_text(template_root.joinpath(filename).read_text())

    config_path = home / "neo.json"
    if force or not config_path.exists():
        HermesNeoConfig(agent_name=agent_name).save(home)

    config_updated = False
    if set_active:
        config_updated = _write_activation_hint(home)

    return {
        "plugin_dir": str(plugin_dir),
        "config_path": str(config_path),
        "set_active": set_active,
        "config_updated": config_updated,
    }


def _write_activation_hint(hermes_home: Path) -> bool:
    """Best-effort activation for single-provider Hermes installs.

    Multi-provider Hermes config is patched in Hermes itself. For Neo's public
    installer, avoid risky YAML surgery and write a small reference file instead.
    """

    hint_path = hermes_home / "neo-hermes-activation.yaml"
    hint_path.write_text(
        "# Add this to Hermes config.yaml to activate Neo as the active memory provider.\n"
        "# If you also use Honcho, prefer Hermes multi-provider support instead.\n"
        "memory:\n"
        "  provider: neo\n"
    )
    return True
