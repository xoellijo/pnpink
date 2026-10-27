# -*- coding: utf-8 -*-
"""Small, shared wrapper for launching Inkscape CLI safely."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import log as LOG
import process_io as PROCESS_IO
import temp_paths as TEMPPATHS

_l = LOG

_EXPORT_SETTLE_TIMEOUT_S = 4.0


def _mac_bundle_executables() -> list[str]:
    if sys.platform != "darwin":
        return []

    candidates: list[Path] = []
    try:
        python_path = Path(sys.executable).resolve()
    except Exception:
        python_path = Path(sys.executable).absolute()
    for parent in (python_path, *python_path.parents):
        if parent.suffix.lower() == ".app":
            candidates.append(parent / "Contents" / "MacOS" / "inkscape")
            break

    for applications_dir in (Path("/Applications"), Path.home() / "Applications"):
        candidates.append(applications_dir / "Inkscape.app" / "Contents" / "MacOS" / "inkscape")
        try:
            candidates.extend(
                bundle / "Contents" / "MacOS" / "inkscape"
                for bundle in sorted(applications_dir.glob("Inkscape*.app"))
            )
        except OSError:
            pass

    output: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = str(candidate)
        if path in seen:
            continue
        seen.add(path)
        if candidate.is_file():
            output.append(path)
    return output


def _parse_query_all(stdout: str, want_ids: set[str] | None = None) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    wanted = set(want_ids or set())
    for line in (stdout or "").splitlines():
        parts = [p for p in re.split(r"[, \t]+", line.strip()) if p]
        if len(parts) < 5:
            continue
        node_id = parts[0]
        if wanted and node_id not in wanted:
            continue
        try:
            x, y, w, h = map(float, parts[1:5])
        except Exception:
            continue
        out[node_id] = {"x": x, "y": y, "width": w, "height": h}
    return out


def find_executable() -> str | None:
    mac_candidates = _mac_bundle_executables()
    if mac_candidates:
        return mac_candidates[0]

    names = ["inkscape.exe", "inkscape"] if os.name == "nt" else ["inkscape"]
    for name in names:
        exe = shutil.which(name)
        if exe:
            return exe

    pyexe = os.path.abspath(sys.executable)
    bin_dir = os.path.dirname(pyexe)
    candidates = [
        os.path.join(bin_dir, "inkscape.exe"),
        os.path.join(os.path.dirname(bin_dir), "inkscape.exe"),
        os.path.join(os.path.dirname(bin_dir), "bin", "inkscape.exe"),
        os.path.join(bin_dir, "inkscape"),
        os.path.join(os.path.dirname(bin_dir), "inkscape"),
        os.path.join(os.path.dirname(bin_dir), "bin", "inkscape"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return None


def executable_dir(exe: str | None) -> str | None:
    return os.path.dirname(str(exe or "")) if exe else None


def shell_executable(exe: str | None) -> str | None:
    """Prefer inkscape.com for --shell on Windows; inkscape.exe can hang on pipes."""
    path = str(exe or "").strip()
    if os.name == "nt" and path.lower().endswith("inkscape.exe"):
        cand = os.path.join(os.path.dirname(path), "inkscape.com")
        if os.path.isfile(cand):
            return cand
    return path or None


def clean_launch_env(*, isolated_profile: bool = True, profile_stem: str = "automation") -> dict[str, str]:
    env = dict(os.environ)
    exact_keys = {
        "SELF_CALL",
        "DOCUMENT_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONIOENCODING",
    }
    prefix_keys = (
        "INKEX_",
        "INKSCAPE_",
        "GDK_",
        "GTK_",
        "XDG_",
    )
    for key in list(env.keys()):
        sk = str(key or "")
        if not sk or sk.startswith("="):
            continue
        if sk in exact_keys or any(sk.startswith(prefix) for prefix in prefix_keys):
            env.pop(sk, None)
    if isolated_profile:
        stem = str(profile_stem or "automation").strip() or "automation"
        try:
            profile_dir = TEMPPATHS.named_dir("inkscape_profile", stem=stem)
            env["INKSCAPE_PROFILE_DIR"] = profile_dir
        except Exception:
            profile_dir = ""
        try:
            env["XDG_CONFIG_HOME"] = TEMPPATHS.named_dir("xdg_config", stem=stem)
        except Exception:
            pass
        try:
            env["XDG_CACHE_HOME"] = TEMPPATHS.named_dir("xdg_cache", stem=stem)
        except Exception:
            pass
        try:
            # GTK writes recently-used.xbel under XDG_DATA_HOME; isolate it for automation runs.
            xdg_data = TEMPPATHS.named_dir("xdg_data", stem=stem)
            env["XDG_DATA_HOME"] = xdg_data
            env["GTK_RECENT_FILES"] = os.path.join(xdg_data, "recently-used.xbel")
        except Exception:
            pass
        if os.name == "nt":
            try:
                appdata = TEMPPATHS.named_dir("win_appdata", stem=stem)
                local_appdata = TEMPPATHS.named_dir("win_localappdata", stem=stem)
                env["APPDATA"] = appdata
                env["LOCALAPPDATA"] = local_appdata
            except Exception:
                pass
        try:
            _l.i(
                "[inkscape_cli] isolated profile='%s' xdg_data='%s'",
                env.get("INKSCAPE_PROFILE_DIR", ""),
                env.get("XDG_DATA_HOME", ""),
            )
        except Exception:
            pass
    return env


def launch_gui(svg_path: str, *, detached: bool = True) -> bool:
    """Open an SVG in the user's normal Inkscape GUI profile.

    This is intentionally separate from CLI automation. GUI opening is the one
    path where we want Inkscape to behave like the user's normal application.
    """
    target = os.path.normpath(os.path.abspath(str(svg_path or "")))
    if not target or not os.path.isfile(target):
        raise FileNotFoundError(target or "missing svg")
    exe = find_executable()
    if not exe:
        return False
    kwargs = {
        "args": [exe, target],
        "cwd": executable_dir(exe),
        "env": clean_launch_env(isolated_profile=False),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = 0
        if detached:
            for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
                creationflags |= int(getattr(subprocess, flag_name, 0))
        creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        kwargs["creationflags"] = creationflags
    elif detached:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(**kwargs)
    if detached:
        try:
            proc.returncode = 0
        except Exception:
            pass
    _l.i("[inkscape_gui] launched svg='%s'", target)
    return True


def run(
    argv: list[str],
    *,
    exe_dir: str | None = None,
    env: dict[str, str] | None = None,
    on_output=None,
    timeout_s: float | None = None,
) -> tuple[int, str]:
    try:
        _l.i("[inkscape_cli] run cwd='%s' argv=%s", exe_dir or "", " ".join(str(part) for part in (argv or [])))
    except Exception:
        pass
    kwargs = {
        "args": argv,
        "cwd": exe_dir or None,
        "env": env if env is not None else clean_launch_env(isolated_profile=True),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_output is None:
        try:
            proc = subprocess.run(**kwargs, timeout=timeout_s if timeout_s and timeout_s > 0 else None)
        except subprocess.TimeoutExpired as ex:
            msg = ((ex.stdout or "") + ("\n" + ex.stderr if ex.stderr else "")).strip()
            _l.w("[inkscape_cli] timeout after %.2fs msg=%s", float(timeout_s or 0), str(msg)[:1000])
            return 124, msg or "timeout"
        msg = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        rc = int(proc.returncode)
    else:
        proc = subprocess.Popen(**kwargs)
        output_q: queue.Queue[str | None] = queue.Queue()

        def _reader(stream):
            try:
                for chunk in PROCESS_IO.iter_text_chunks(stream):
                    output_q.put(chunk)
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        readers = [
            threading.Thread(target=_reader, args=(proc.stdout,), daemon=True),
            threading.Thread(target=_reader, args=(proc.stderr,), daemon=True),
        ]
        for reader in readers:
            reader.start()
        chunks: list[str] = []
        while proc.poll() is None or any(reader.is_alive() for reader in readers) or not output_q.empty():
            try:
                chunk = output_q.get(timeout=0.05)
            except queue.Empty:
                continue
            if chunk is None:
                continue
            chunks.append(chunk)
            try:
                on_output(chunk)
            except Exception:
                pass
        for reader in readers:
            try:
                reader.join(timeout=0.2)
            except Exception:
                pass
        rc = int(proc.returncode or 0)
        msg = "".join(chunks).strip()
    if rc == 0:
        _l.i("[inkscape_cli] ok rc=%d", rc)
    else:
        _l.w("[inkscape_cli] failed rc=%d msg=%s", rc, msg[:1000])
    return rc, msg


def run_shell_commands(
    exe: str,
    commands: list[str],
    *,
    exe_dir: str | None = None,
    env: dict[str, str] | None = None,
    on_output=None,
    timeout_s: float | None = None,
    shell_args: list[str] | None = None,
) -> tuple[int, str]:
    script = [str(cmd or "").strip() for cmd in (commands or []) if str(cmd or "").strip()]
    if not script:
        return 0, ""
    exe = shell_executable(exe) or exe
    payload = "\n".join(script + ["quit"]) + "\n"
    _l.i("[inkscape_shell] start exe='%s' cwd='%s' commands=%d", exe, exe_dir or "", len(script))
    for idx, cmd in enumerate(script, start=1):
        _l.i("[inkscape_shell] cmd%02d %s", idx, cmd)
    kwargs = {
        "args": [exe] + [str(arg) for arg in (shell_args or [])] + ["--shell"],
        "cwd": exe_dir or None,
        "env": env if env is not None else clean_launch_env(isolated_profile=True),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc = subprocess.Popen(stdin=subprocess.PIPE, **kwargs)
    output_q: queue.Queue[str | None] = queue.Queue()

    def _reader(stream):
        try:
            for chunk in PROCESS_IO.iter_text_chunks(stream):
                output_q.put(chunk)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    readers = [
        threading.Thread(target=_reader, args=(proc.stdout,), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        assert proc.stdin is not None
        proc.stdin.write(payload)
        proc.stdin.close()
    except Exception:
        pass

    chunks: list[str] = []
    deadline = time.perf_counter() + float(timeout_s) if timeout_s and timeout_s > 0 else None
    timed_out = False
    try:
        while proc.poll() is None or any(reader.is_alive() for reader in readers) or not output_q.empty():
            if deadline is not None and time.perf_counter() > deadline:
                timed_out = True
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
                break
            try:
                line = output_q.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                continue
            chunks.append(line)
            if on_output is not None:
                try:
                    on_output(line)
                except Exception:
                    pass
    finally:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
        for reader in readers:
            try:
                reader.join(timeout=0.2)
            except Exception:
                pass
        while not output_q.empty():
            try:
                line = output_q.get_nowait()
            except queue.Empty:
                break
            if line is None:
                continue
            chunks.append(line)
            if on_output is not None:
                try:
                    on_output(line)
                except Exception:
                    pass
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
    try:
        proc.wait(timeout=0.1)
    except Exception:
        pass
    if proc.returncode is None:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
    rc = 124 if timed_out else int(proc.returncode or 0)
    msg = "".join(chunks).strip()
    if timed_out:
        _l.w("[inkscape_shell] timeout after %.2fs msg=%s", float(timeout_s or 0), msg[:1000])
        return rc, msg or "timeout"
    if rc == 0:
        _l.i("[inkscape_shell] ok rc=%d", int(proc.returncode))
    else:
        _l.w("[inkscape_shell] failed rc=%d msg=%s", rc, msg[:1000])
    return rc, msg


class ShellQuerySession:
    """Persistent `inkscape --shell` process for repeated query-all probes."""

    def __init__(self, exe: str, *, exe_dir: str | None = None, env: dict[str, str] | None = None):
        self.exe = exe
        self.exe_dir = exe_dir
        self.env = env
        self.proc: subprocess.Popen | None = None
        self.output_q: queue.Queue[str] = queue.Queue()
        self.readers: list[threading.Thread] = []
        self.current_svg: str | None = None
        self.ready = False

    def __enter__(self):
        kwargs = {
            "args": [self.exe, "--shell"],
            "cwd": self.exe_dir or None,
            "env": self.env if self.env is not None else clean_launch_env(isolated_profile=True),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.proc = subprocess.Popen(**kwargs)

        def _reader(stream):
            try:
                for chunk in PROCESS_IO.iter_text_chunks(stream):
                    self.output_q.put(chunk)
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        self.readers = [
            threading.Thread(target=_reader, args=(self.proc.stdout,), daemon=True),
            threading.Thread(target=_reader, args=(self.proc.stderr,), daemon=True),
        ]
        for reader in self.readers:
            reader.start()
        _l.i("[inkscape_shell_query] start exe='%s' cwd='%s'", self.exe, self.exe_dir or "")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _drain(self) -> None:
        while True:
            try:
                self.output_q.get_nowait()
            except queue.Empty:
                break

    def _send(self, command: str) -> None:
        if self.proc is None or self.proc.stdin is None or self.proc.poll() is not None:
            raise RuntimeError("Inkscape shell is not running")
        self.proc.stdin.write(command.rstrip("\n") + "\n")
        self.proc.stdin.flush()

    def _read_until_prompt(self, *, timeout_s: float) -> str:
        deadline = time.perf_counter() + max(0.1, float(timeout_s))
        chunks: list[str] = []
        while time.perf_counter() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                break
            try:
                chunks.append(self.output_q.get(timeout=0.02))
            except queue.Empty:
                continue
            text = "".join(chunks)
            if text.endswith("\n> ") or (len(text) < 500 and text.endswith("> ")):
                return text
        return "".join(chunks)

    def wait_ready(self, *, timeout_s: float = 20.0) -> None:
        if self.ready:
            return
        output = self._read_until_prompt(timeout_s=timeout_s)
        if not output.endswith("> "):
            return_code = self.proc.poll() if self.proc is not None else None
            detail = output.strip().replace("\n", " ")[:1000]
            raise RuntimeError(
                f"Inkscape shell did not become ready: exe='{self.exe}' "
                f"rc={return_code} output={detail!r}"
            )
        self.ready = True

    def _action(self, command: str, *, timeout_s: float) -> str:
        self.wait_ready(timeout_s=timeout_s)
        self._drain()
        self._send(command)
        output = self._read_until_prompt(timeout_s=timeout_s)
        if not output.endswith("> "):
            raise RuntimeError(f"Inkscape shell action timed out: {command}")
        return output

    def query_all(
        self,
        svg_path: str,
        ids: set[str],
        *,
        timeout_s: float = 8.0,
        open_delay_s: float = 0.0,
        log_query: bool = True,
    ) -> dict[str, dict[str, float]]:
        ids = set(ids or set())
        if not ids:
            return {}
        t0 = time.perf_counter()
        self._action(f"file-open:{svg_path}", timeout_s=timeout_s)
        self.current_svg = os.path.normcase(os.path.normpath(str(svg_path)))
        if open_delay_s and open_delay_s > 0:
            time.sleep(float(open_delay_s))
        out = self._action("query-all", timeout_s=timeout_s)
        bbs = _parse_query_all(out, ids)
        dt = (time.perf_counter() - t0) * 1000.0
        if log_query:
            _l.i("[inkscape_shell_query] query file='%s' ids=%d bboxes=%d ms=%.1f", svg_path, len(ids), len(bbs), dt)
            _l.i("[inkscape_shell_query] raw_output=%s", repr((out or "")[:2000]))
        if not bbs and out:
            _l.w("[inkscape_shell_query] no bboxes; output=%s", out[:1000])
        return bbs

    def export_area_png(
        self,
        svg_path: str,
        png_path: str,
        area: tuple[float, float, float, float],
        *,
        dpi: int = 150,
        compression: int = 1,
        background_color: str = "#ffffff",
        background_opacity: str = "255",
        reload_svg: bool = True,
        timeout_s: float = 20.0,
    ) -> None:
        """Export one SVG area through this already-running shell."""
        x0, y0, x1, y1 = (float(value) for value in area)
        try:
            os.remove(png_path)
        except FileNotFoundError:
            pass
        commands = []
        if reload_svg:
            commands.append(f"file-open:{svg_path}")
        commands.extend([
            "export-type:png",
            f"export-dpi:{int(dpi)}",
            f"export-png-compression:{max(0, min(9, int(compression)))}",
            f"export-area:{x0:.6f}:{y0:.6f}:{x1:.6f}:{y1:.6f}",
            f"export-background:{background_color}",
            f"export-background-opacity:{background_opacity}",
            f"export-filename:{png_path}",
            "export-do",
        ])
        output = self._action(";".join(commands), timeout_s=timeout_s)
        settle_timeout = min(_EXPORT_SETTLE_TIMEOUT_S, timeout_s)
        if not self._wait_for_export(png_path, timeout_s=settle_timeout):
            detail = self._export_failure_detail(png_path, output, settle_timeout)
            _l.w("[inkscape_shell_preview] area export fallback: %s", detail)
            self._export_png_once(
                svg_path,
                png_path,
                area=(x0, y0, x1, y1),
                dpi=dpi,
                compression=compression,
                background_color=background_color,
                background_opacity=background_opacity,
                timeout_s=timeout_s,
            )

    def export_id_png(
        self,
        svg_path: str,
        png_path: str,
        node_id: str,
        *,
        dpi: int = 150,
        compression: int = 1,
        background_color: str = "#ffffff",
        background_opacity: str = "255",
        reload_svg: bool = True,
        timeout_s: float = 20.0,
    ) -> None:
        """Export one SVG element through this already-running shell."""
        try:
            os.remove(png_path)
        except FileNotFoundError:
            pass
        commands = []
        if reload_svg:
            commands.append(f"file-open:{svg_path}")
        commands.extend([
            "export-type:png",
            f"export-dpi:{int(dpi)}",
            f"export-png-compression:{max(0, min(9, int(compression)))}",
            f"export-id:{node_id}",
            "export-id-only:true",
            f"export-background:{background_color}",
            f"export-background-opacity:{background_opacity}",
            f"export-filename:{png_path}",
            "export-do",
        ])
        output = self._action(";".join(commands), timeout_s=timeout_s)
        settle_timeout = min(_EXPORT_SETTLE_TIMEOUT_S, timeout_s)
        if not self._wait_for_export(png_path, timeout_s=settle_timeout):
            detail = self._export_failure_detail(png_path, output, settle_timeout)
            _l.w("[inkscape_shell_preview] id export fallback: %s", detail)
            self._export_png_once(
                svg_path,
                png_path,
                node_id=node_id,
                dpi=dpi,
                compression=compression,
                background_color=background_color,
                background_opacity=background_opacity,
                timeout_s=timeout_s,
            )

    def _export_png_once(
        self,
        svg_path: str,
        png_path: str,
        *,
        area: tuple[float, float, float, float] | None = None,
        node_id: str = "",
        dpi: int,
        compression: int,
        background_color: str,
        background_opacity: str,
        timeout_s: float,
    ) -> None:
        argv = [
            self.exe,
            svg_path,
            "--export-type=png",
            f"--export-dpi={int(dpi)}",
            f"--export-png-compression={max(0, min(9, int(compression)))}",
            f"--export-background={background_color}",
            f"--export-background-opacity={background_opacity}",
            f"--export-filename={png_path}",
        ]
        if area is not None:
            x0, y0, x1, y1 = area
            argv.append(f"--export-area={x0:.6f}:{y0:.6f}:{x1:.6f}:{y1:.6f}")
        elif node_id:
            argv.extend((f"--export-id={node_id}", "--export-id-only"))
        return_code, command_output = run(
            argv,
            exe_dir=self.exe_dir,
            env=self.env,
            timeout_s=timeout_s,
        )
        if return_code != 0 or not self._wait_for_export(png_path, timeout_s=1.0):
            detail = self._export_failure_detail(png_path, command_output, 1.0)
            raise RuntimeError(
                f"Inkscape could not create the preview PNG "
                f"(shell and one-shot export failed, rc={return_code}); {detail}"
            )

    @staticmethod
    def _export_failure_detail(path: str, output: str, timeout_s: float) -> str:
        shell_output = " ".join(str(output or "").split())[-1000:]
        return (
            f"path={path!r} wait={float(timeout_s):.1f}s "
            f"shell_output={shell_output!r}"
        )

    @staticmethod
    def _wait_for_export(path: str, *, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        while time.monotonic() < deadline:
            try:
                if os.path.isfile(path) and os.path.getsize(path) > 16:
                    return True
            except OSError:
                pass
            time.sleep(0.02)
        return False

    def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.stdin is not None and proc.poll() is None:
                proc.stdin.write("quit\n")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        for reader in self.readers:
            try:
                reader.join(timeout=0.2)
            except Exception:
                pass
        _l.i("[inkscape_shell_query] closed rc=%s", getattr(proc, "returncode", None))


def build_pdf_export_argv(
    exe: str,
    svg_path: str,
    pdf_path: str,
    *,
    page_selector: str | None = None,
    dpi: int = 300,
    ignore_filters: bool = False,
) -> list[str]:
    argv = [
        exe,
        svg_path,
        "--export-type=pdf",
        f"--export-filename={pdf_path}",
        f"--export-dpi={int(dpi)}",
    ]
    if ignore_filters:
        argv.append("--export-ignore-filters")
    if page_selector:
        argv.append(f"--export-page={page_selector}")
    return argv


def build_page_export_argv(
    exe: str,
    svg_path: str,
    out_path: str,
    *,
    export_type: str,
    page_selector: str | None = None,
    dpi: int = 300,
) -> list[str]:
    export_name = str(export_type or "png").strip().lower()
    argv = [
        exe,
        svg_path,
        f"--export-type={export_name}",
        f"--export-filename={out_path}",
    ]
    if export_name == "png":
        argv.append(f"--export-dpi={int(dpi)}")
    if page_selector:
        argv.append(f"--export-page={page_selector}")
    return argv


def build_export_id_png_argv(
    exe: str,
    svg_path: str,
    node_id: str,
    png_path: str,
    *,
    dpi: int,
    png_antialias: int | None = None,
    png_use_dithering: bool | None = None,
    background_color: str | None = None,
    background_opacity: str | None = None,
) -> list[str]:
    argv = [
        exe,
        svg_path,
        f"--export-id={node_id}",
        "--export-id-only",
        f"--export-dpi={int(dpi)}",
        f"--export-filename={png_path}",
    ]
    if png_antialias is not None:
        argv.append(f"--export-png-antialias={int(png_antialias)}")
    if png_use_dithering is not None:
        argv.append(f"--export-png-use-dithering={'true' if bool(png_use_dithering) else 'false'}")
    if background_color is not None and str(background_color).strip():
        argv.append(f"--export-background={str(background_color).strip()}")
    if background_opacity is not None and str(background_opacity).strip():
        argv.append(f"--export-background-opacity={str(background_opacity).strip()}")
    return argv


def build_shell_png_page_commands(
    svg_path: str,
    page_exports: list[tuple[int, str]],
    *,
    dpi: int = 300,
) -> list[str]:
    page_areas = svg_page_areas(svg_path)
    units_per_inch = svg_user_units_per_inch(svg_path)
    commands = [f"file-open:{svg_path}"]
    for page_no, png_path in page_exports:
        area = page_areas.get(int(page_no))
        if area is None:
            raise ValueError(f"SVG page {page_no} not found: {svg_path}")
        pixel_width = _page_pixel_width(area, units_per_inch, dpi)
        commands.extend(
            [
                f"export-area:{_format_export_area(area)}",
                f"export-filename:{png_path}",
                "export-type:png",
                f"export-width:{pixel_width}",
                "export-do",
            ]
        )
    return commands


def build_shell_page_export_commands(
    svg_path: str,
    page_exports: list[tuple[int, str]],
    *,
    export_type: str,
    dpi: int = 300,
) -> list[str]:
    export_name = str(export_type or "png").strip().lower()
    page_areas = svg_page_areas(svg_path)
    units_per_inch = svg_user_units_per_inch(svg_path)
    commands = [f"file-open:{svg_path}"]
    for page_no, out_path in page_exports:
        area = page_areas.get(int(page_no))
        if area is None:
            raise ValueError(f"SVG page {page_no} not found: {svg_path}")
        commands.extend(
            [
                f"export-area:{_format_export_area(area)}",
                f"export-filename:{out_path}",
                f"export-type:{export_name}",
            ]
        )
        if export_name == "png":
            commands.append(f"export-width:{_page_pixel_width(area, units_per_inch, dpi)}")
        commands.append("export-do")
    return commands


def svg_page_areas(svg_path: str) -> dict[int, tuple[float, float, float, float]]:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    pages = root.findall(".//{http://www.inkscape.org/namespaces/inkscape}page")
    areas: dict[int, tuple[float, float, float, float]] = {}
    for page_no, page in enumerate(pages, start=1):
        x = float(page.get("x") or 0.0)
        y = float(page.get("y") or 0.0)
        width = float(page.get("width") or 0.0)
        height = float(page.get("height") or 0.0)
        if width > 0 and height > 0:
            areas[page_no] = (x, y, x + width, y + height)
    if areas:
        return areas

    view_box = str(root.get("viewBox") or "").replace(",", " ").split()
    if len(view_box) == 4:
        x, y, width, height = (float(value) for value in view_box)
        if width > 0 and height > 0:
            return {1: (x, y, x + width, y + height)}
    raise ValueError(f"SVG has no valid page geometry: {svg_path}")


def _format_export_area(area: tuple[float, float, float, float]) -> str:
    return ":".join(f"{value:.9g}" for value in area)


def svg_user_units_per_inch(svg_path: str) -> float:
    root = ET.parse(svg_path).getroot()
    view_box = str(root.get("viewBox") or "").replace(",", " ").split()
    if len(view_box) == 4:
        view_width = float(view_box[2])
        physical_width = _svg_length_inches(root.get("width"))
        if view_width > 0 and physical_width is not None and physical_width > 0:
            return view_width / physical_width
    return 96.0


def _svg_length_inches(value: str | None) -> float | None:
    match = re.fullmatch(
        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(px|mm|cm|in|pt|pc|q)?\s*",
        str(value or ""),
        re.IGNORECASE,
    )
    if not match:
        return None
    amount = float(match.group(1))
    unit = str(match.group(2) or "px").lower()
    units_per_inch = {
        "px": 96.0,
        "mm": 25.4,
        "cm": 2.54,
        "in": 1.0,
        "pt": 72.0,
        "pc": 6.0,
        "q": 101.6,
    }
    return amount / units_per_inch[unit]


def _page_pixel_width(
    area: tuple[float, float, float, float],
    units_per_inch: float,
    dpi: int,
) -> int:
    width = max(0.0, float(area[2]) - float(area[0]))
    return max(1, int(round(width / max(float(units_per_inch), 1e-12) * max(1, int(dpi)))))


def build_shell_export_id_png_commands(
    svg_path: str,
    exports: list[tuple[str, str, int]],
    *,
    png_antialias: int | None = None,
    png_use_dithering: bool | None = None,
    background_color: str | None = None,
    background_opacity: str | None = None,
) -> list[str]:
    commands = [f"file-open:{svg_path}"]
    if exports:
        commands.append("export-type:png")
        commands.append(f"export-dpi:{int(exports[0][2])}")
    for node_id, png_path, dpi in exports:
        commands.extend(
            [
                f"export-id:{node_id}",
                "export-id-only:true",
                f"export-filename:{png_path}",
            ]
        )
        if png_antialias is not None:
            commands.append(f"export-png-antialias:{int(png_antialias)}")
        if png_use_dithering is not None:
            commands.append(f"export-png-use-dithering:{'true' if bool(png_use_dithering) else 'false'}")
        if background_color is not None and str(background_color).strip():
            commands.append(f"export-background:{str(background_color).strip()}")
        if background_opacity is not None and str(background_opacity).strip():
            commands.append(f"export-background-opacity:{str(background_opacity).strip()}")
        commands.append("export-do")
    return commands


def build_shell_export_area_png_commands(
    exports: list[tuple[str, tuple[float, float, float, float], str, int]],
) -> list[str]:
    commands: list[str] = []
    current_svg = None
    for svg_path, area, png_path, dpi in exports:
        if svg_path != current_svg:
            commands.append(f"file-open:{svg_path}")
            commands.append("export-type:png")
            commands.append(f"export-dpi:{int(dpi)}")
            current_svg = svg_path
        x0, y0, x1, y1 = area
        commands.extend(
            [
                f"export-area:{x0:.6f}:{y0:.6f}:{x1:.6f}:{y1:.6f}",
                f"export-filename:{png_path}",
                "export-do",
            ]
        )
    return commands
