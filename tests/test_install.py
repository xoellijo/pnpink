from pathlib import Path

import install


def test_pnp_python_can_be_loaded_from_payload(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(install, "__file__", str(tmp_path / "launcher" / "install.py"))
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pnp_python.py").write_text(
        "class PnPPythonError(Exception):\n    pass\n"
        "def shared_python_root():\n    return 'payload-runtime'\n"
        "def ensure_project_environment(*args):\n    return args\n",
        encoding="utf-8",
    )

    runtime = install.load_pnp_python(payload)

    assert runtime.shared_python_root() == "payload-runtime"
