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

import log as LOG
import temp_paths as TEMPPATHS

_l = LOG


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


def _parse_last_float_lines(stdout: str, count: int) -> list[float]:
    vals: list[float] = []
    for line in (stdout or "").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            vals.append(float(s))
        except Exception:
            continue
    if count <= 0:
        return vals
    return vals[-count:]


def find_executable() -> str | None:
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


def clean_launch_env(*, isolated_profile: bool = True) -> dict[str, str]:
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
        try:
            profile_dir = TEMPPATHS.named_dir("inkscape_profile", stem="automation")
            env["INKSCAPE_PROFILE_DIR"] = profile_dir
        except Exception:
            profile_dir = ""
        try:
            env["XDG_CONFIG_HOME"] = TEMPPATHS.named_dir("xdg_config", stem="automation")
        except Exception:
            pass
        try:
            env["XDG_CACHE_HOME"] = TEMPPATHS.named_dir("xdg_cache", stem="automation")
        except Exception:
            pass
        try:
            # GTK writes recently-used.xbel under XDG_DATA_HOME; isolate it for automation runs.
            xdg_data = TEMPPATHS.named_dir("xdg_data", stem="automation")
            env["XDG_DATA_HOME"] = xdg_data
            env["GTK_RECENT_FILES"] = os.path.join(xdg_data, "recently-used.xbel")
        except Exception:
            pass
        if os.name == "nt":
            try:
                appdata = TEMPPATHS.named_dir("win_appdata", stem="automation")
                local_appdata = TEMPPATHS.named_dir("win_localappdata", stem="automation")
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
                while True:
                    chunk = stream.read(1)
                    if not chunk:
                        break
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
            while True:
                chunk = stream.read(1)
                if not chunk:
                    break
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
                while True:
                    chunk = stream.read(1)
                    if not chunk:
                        break
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
            raise RuntimeError("Inkscape shell did not become ready")
        self.ready = True

    def _action(self, command: str, *, timeout_s: float) -> str:
        self.wait_ready(timeout_s=timeout_s)
        self._drain()
        self._send(command)
        output = self._read_until_prompt(timeout_s=timeout_s)
        if not output.endswith("> "):
            raise RuntimeError(f"Inkscape shell action timed out: {command}")
        return output

    def _read_until_bboxes(self, ids: set[str], *, timeout_s: float) -> tuple[dict[str, dict[str, float]], str]:
        deadline = time.perf_counter() + max(0.1, float(timeout_s))
        chunks: list[str] = []
        bbs: dict[str, dict[str, float]] = {}
        while time.perf_counter() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                break
            try:
                chunk = self.output_q.get(timeout=0.05)
            except queue.Empty:
                continue
            chunks.append(chunk)
            text = "".join(chunks)
            bbs = _parse_query_all(text, ids)
            if ids and ids.issubset(set(bbs.keys())):
                return bbs, text
        text = "".join(chunks)
        return _parse_query_all(text, ids), text

    def _read_until_float_count(self, count: int, *, timeout_s: float) -> tuple[list[float], str]:
        deadline = time.perf_counter() + max(0.1, float(timeout_s))
        chunks: list[str] = []
        vals: list[float] = []
        while time.perf_counter() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                break
            try:
                chunk = self.output_q.get(timeout=0.05)
            except queue.Empty:
                continue
            chunks.append(chunk)
            text = "".join(chunks)
            vals = _parse_last_float_lines(text, count)
            if len(vals) >= count:
                return vals[-count:], text
        text = "".join(chunks)
        return _parse_last_float_lines(text, count), text

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

    def query_metrics(
        self,
        svg_path: str,
        node_id: str,
        *,
        timeout_s: float = 4.0,
        open_delay_s: float = 0.0,
    ) -> tuple[dict[str, float], str]:
        node_id = str(node_id or "").strip()
        if not node_id:
            return {}, ""
        self._drain()
        self._send(f"file-open:{svg_path}")
        self.current_svg = os.path.normcase(os.path.normpath(str(svg_path)))
        if open_delay_s and open_delay_s > 0:
            time.sleep(float(open_delay_s))
        self._send(f"query-x:{node_id}")
        self._send(f"query-y:{node_id}")
        self._send(f"query-width:{node_id}")
        self._send(f"query-height:{node_id}")
        vals, out = self._read_until_float_count(4, timeout_s=timeout_s)
        metrics: dict[str, float] = {}
        if len(vals) >= 4:
            metrics = {
                "x": float(vals[-4]),
                "y": float(vals[-3]),
                "width": float(vals[-2]),
                "height": float(vals[-1]),
            }
        _l.i("[inkscape_shell_query] direct_metrics id='%s' metrics=%s", node_id, metrics or {})
        _l.i("[inkscape_shell_query] direct_raw_output=%s", repr((out or "")[:1000]))
        return metrics, out

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
    commands = [f"file-open:{svg_path}"]
    for page_no, png_path in page_exports:
        commands.extend(
            [
                f"export-page:{int(page_no)}",
                f"export-filename:{png_path}",
                "export-type:png",
                f"export-dpi:{int(dpi)}",
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
    commands = [f"file-open:{svg_path}"]
    for page_no, out_path in page_exports:
        commands.extend(
            [
                f"export-page:{int(page_no)}",
                f"export-filename:{out_path}",
                f"export-type:{export_name}",
            ]
        )
        if export_name == "png":
            commands.append(f"export-dpi:{int(dpi)}")
        commands.append("export-do")
    return commands


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
