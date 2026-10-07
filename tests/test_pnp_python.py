import json
import os
import sys
from pathlib import Path

import pytest

import pnp_python


@pytest.mark.skipif(os.name != "nt", reason="Windows data-root convention")
def test_windows_shared_root_is_next_to_icc_profiles(monkeypatch, tmp_path):
    monkeypatch.delenv("PNP_PYTHON_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert pnp_python.pnpink_data_root() == tmp_path / "PnPInk"
    assert pnp_python.shared_python_root() == tmp_path / "PnPInk" / "python"


def test_projects_share_one_combined_environment(monkeypatch, tmp_path):
    root = tmp_path / "PnPInk" / "python"
    pip_path = root / "bootstrap" / pnp_python.interpreter_tag() / "pip.pyz"
    installed: list[tuple[str, ...]] = []

    def fake_ensure_pip(_root):
        if not pip_path.is_file():
            pip_path.parent.mkdir(parents=True, exist_ok=True)
            pip_path.write_bytes(b"pip")
        return pip_path

    def fake_run_pip(_pip_path, target, requirements, _root):
        installed.append(tuple(requirements))
        (target / "installed.txt").write_text("\n".join(requirements), encoding="utf-8")

    monkeypatch.setattr(pnp_python, "ensure_pip", fake_ensure_pip)
    monkeypatch.setattr(pnp_python, "_run_pip", fake_run_pip)
    monkeypatch.setattr(pnp_python, "_verify_imports", lambda *_args: None)

    first = pnp_python.ensure_project_environment("pnpink", (), (), root)
    second = pnp_python.ensure_project_environment(
        "pnpplay",
        ("aiohttp==3.14.3", "PyYAML==6.0.3"),
        ("aiohttp", "yaml"),
        root,
    )

    assert first != second
    assert installed == [(), ("aiohttp==3.14.3", "PyYAML==6.0.3")]
    assert json.loads((root / "requirements" / "pnpink.json").read_text(encoding="utf-8"))["requirements"] == []
    assert json.loads((root / "requirements" / "pnpplay.json").read_text(encoding="utf-8"))["requirements"] == [
        "aiohttp==3.14.3",
        "PyYAML==6.0.3",
    ]
    assert pnp_python.current_site_packages(root) == second


def test_activate_current_environment_prepends_site_packages(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    environment = "test-environment"
    site_packages = root / "environments" / environment / "site-packages"
    site_packages.mkdir(parents=True)
    pointer = root / "current" / f"{pnp_python.interpreter_tag()}.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"environment": environment}), encoding="utf-8")
    monkeypatch.setattr(sys, "path", list(sys.path))

    activated = pnp_python.activate_current_environment(root)

    assert activated == site_packages
    assert sys.path[0] == str(site_packages)


def test_mingw_inkscape_python_uses_standard_windows_wheels(monkeypatch):
    monkeypatch.setattr(pnp_python.os, "name", "nt")
    monkeypatch.setattr(pnp_python.sysconfig, "get_platform", lambda: "mingw_x86_64_ucrt_gnu")
    monkeypatch.setattr(pnp_python.platform, "machine", lambda: "AMD64")

    arguments = pnp_python._pip_compatibility_args()

    assert arguments[:2] == ["--platform", "win_amd64"]
    assert arguments[-2:] == ["--abi", f"cp{sys.version_info.major}{sys.version_info.minor}"]
