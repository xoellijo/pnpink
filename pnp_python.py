"""Shared Python dependency runtime for PnPInk and PnPPlay.

This module intentionally uses only the Python standard library.  It is copied
into both projects so either installer can bootstrap the same per-user runtime
with Inkscape's Python interpreter.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable, Iterator, Sequence


BOOTSTRAP_API_VERSION = 1
PIP_URL = "https://bootstrap.pypa.io/pip/pip.pyz"
LOCK_TIMEOUT_SECONDS = 600
STALE_LOCK_SECONDS = 1800


class PnPPythonError(RuntimeError):
    """Raised when the shared dependency runtime cannot be prepared."""


def pnpink_data_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "PnPInk"
        return Path.home() / "AppData" / "Local" / "PnPInk"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PnPInk"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".local" / "share") / "PnPInk"


def shared_python_root() -> Path:
    override = str(os.environ.get("PNP_PYTHON_ROOT") or "").strip()
    return Path(override).expanduser().resolve() if override else pnpink_data_root() / "python"


def interpreter_tag() -> str:
    values = (
        sys.implementation.name,
        sys.implementation.cache_tag or f"py{sys.version_info.major}{sys.version_info.minor}",
        sys.platform,
        platform.machine() or "unknown",
    )
    return "-".join(re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-") for value in values)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _project_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ValueError("Project name cannot be empty")
    return normalized


def _requirements(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        requirement = str(value).strip()
        if requirement and not requirement.startswith("#") and requirement not in result:
            result.append(requirement)
    return result


def _manifest(project: str, requirements: Iterable[str], imports: Iterable[str]) -> dict[str, object]:
    return {
        "schema": 1,
        "project": _project_name(project),
        "requirements": _requirements(requirements),
        "imports": sorted(set(filter(None, (str(value).strip() for value in imports)))),
    }


def _load_manifests(root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in sorted((root / "requirements").glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        project = _project_name(str(value.get("project") or path.stem))
        result[project] = _manifest(project, value.get("requirements", []), value.get("imports", []))
    return result


def _combined(manifests: dict[str, dict[str, object]]) -> tuple[list[str], list[str]]:
    requirements: list[str] = []
    imports: set[str] = set()
    for project in sorted(manifests):
        for requirement in manifests[project].get("requirements", []):
            value = str(requirement)
            if value not in requirements:
                requirements.append(value)
        imports.update(str(value) for value in manifests[project].get("imports", []))
    return requirements, sorted(imports)


def _environment_name(requirements: Sequence[str]) -> str:
    payload = json.dumps(list(requirements), separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"{interpreter_tag()}-{hashlib.sha256(payload).hexdigest()[:16]}"


@contextlib.contextmanager
def _installation_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "install.lock"
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(f"{os.getpid()}\n{time.time()}\n")
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > STALE_LOCK_SECONDS:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise PnPPythonError(f"Timed out waiting for the shared Python runtime lock: {lock}")
            time.sleep(0.2)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def ensure_pip(root: Path | None = None) -> Path:
    root = root or shared_python_root()
    destination = root / "bootstrap" / interpreter_tag() / "pip.pyz"
    if destination.is_file() and destination.stat().st_size:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    request = urllib.request.Request(
        str(os.environ.get("PNP_PIP_URL") or PIP_URL),
        headers={"User-Agent": "PnPInk-Python-Bootstrap/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        with zipfile.ZipFile(temporary, "r") as archive:
            if "__main__.py" not in archive.namelist():
                raise PnPPythonError("Downloaded pip.pyz is not a valid pip zip application")
        os.replace(temporary, destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, PnPPythonError):
            raise
        raise PnPPythonError(f"Could not download pip.pyz: {exc}") from exc
    return destination


def _run_pip(pip_path: Path, target: Path, requirements: Sequence[str], root: Path) -> None:
    if not requirements:
        return
    requirements_file = target.parent / "requirements.txt"
    requirements_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    command = [
        sys.executable,
        "-I",
        str(pip_path),
        "--isolated",
        "--disable-pip-version-check",
        "install",
        "--no-input",
        "--ignore-installed",
        "--only-binary=:all:",
        "--no-compile",
        *_pip_compatibility_args(),
        "--cache-dir",
        str(root / "cache"),
        "--target",
        str(target),
        "--requirement",
        str(requirements_file),
    ]
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(command, env=environment, check=False)
    if result.returncode:
        raise PnPPythonError(f"pip.pyz failed with exit code {result.returncode}")


def _pip_compatibility_args() -> list[str]:
    """Use standard Windows wheels with Inkscape's MinGW CPython build.

    PyPI generally does not publish ``mingw_*`` wheels. Inkscape's Python can
    use the pure-Python fallbacks and bundled DLLs from the regular Windows
    wheels used by the existing PnPPlay runtime, so pip must resolve those
    wheels explicitly instead of filtering them out by platform tag.
    """
    if os.name != "nt" or not sysconfig.get_platform().lower().startswith("mingw"):
        return []
    machine = (platform.machine() or "").lower()
    if machine in {"amd64", "x86_64"}:
        target_platform = "win_amd64"
    elif machine in {"arm64", "aarch64"}:
        target_platform = "win_arm64"
    else:
        target_platform = "win32"
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
    return [
        "--platform",
        target_platform,
        "--implementation",
        "cp",
        "--python-version",
        version,
        "--abi",
        abi,
    ]


def _verify_imports(site_packages: Path, imports: Sequence[str]) -> None:
    if not imports:
        return
    script = (
        "import importlib,sys;"
        f"sys.path.insert(0,{str(site_packages)!r});"
        f"[importlib.import_module(name) for name in {list(imports)!r}]"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-I", "-S", "-c", script], env=environment, check=False)
    if result.returncode:
        raise PnPPythonError(f"Shared Python dependency import check failed with exit code {result.returncode}")


def ensure_project_environment(
    project: str,
    requirements: Iterable[str] = (),
    imports: Iterable[str] = (),
    root: Path | None = None,
) -> Path:
    root = (root or shared_python_root()).expanduser().resolve()
    candidate = _manifest(project, requirements, imports)
    with _installation_lock(root):
        pip_path = ensure_pip(root)
        manifests = _load_manifests(root)
        manifests[str(candidate["project"])] = candidate
        combined_requirements, combined_imports = _combined(manifests)
        environment_name = _environment_name(combined_requirements)
        environment_root = root / "environments" / environment_name
        site_packages = environment_root / "site-packages"
        marker = environment_root / "environment.json"
        if not marker.is_file() or not site_packages.is_dir():
            environments = root / "environments"
            environments.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=f".{environment_name}-", dir=environments))
            try:
                temporary_site = temporary / "site-packages"
                temporary_site.mkdir()
                _run_pip(pip_path, temporary_site, combined_requirements, root)
                _verify_imports(temporary_site, combined_imports)
                _atomic_json(
                    temporary / "environment.json",
                    {
                        "schema": 1,
                        "bootstrapApi": BOOTSTRAP_API_VERSION,
                        "interpreter": interpreter_tag(),
                        "requirements": combined_requirements,
                        "imports": combined_imports,
                        "created": int(time.time()),
                    },
                )
                try:
                    os.replace(temporary, environment_root)
                except OSError:
                    if not marker.is_file():
                        raise
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)
        _atomic_json(root / "requirements" / f"{candidate['project']}.json", candidate)
        _atomic_json(
            root / "current" / f"{interpreter_tag()}.json",
            {"schema": 1, "environment": environment_name},
        )
        return site_packages


def current_site_packages(root: Path | None = None) -> Path | None:
    root = (root or shared_python_root()).expanduser().resolve()
    pointer = root / "current" / f"{interpreter_tag()}.json"
    try:
        value = json.loads(pointer.read_text(encoding="utf-8"))
        site_packages = root / "environments" / str(value["environment"]) / "site-packages"
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        return site_packages if site_packages.is_dir() else None
    except OSError:
        return None


def activate_current_environment(root: Path | None = None) -> Path:
    site_packages = current_site_packages(root)
    if site_packages is None:
        raise PnPPythonError("The shared PnP Python runtime is not installed for this interpreter")
    value = str(site_packages)
    if value not in sys.path:
        sys.path.insert(0, value)
    importlib.invalidate_caches()
    return site_packages
