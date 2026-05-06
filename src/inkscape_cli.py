# -*- coding: utf-8 -*-
"""Small, shared wrapper for launching Inkscape CLI safely."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading

import log as LOG
import temp_paths as TEMPPATHS

_l = LOG


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
            env["INKSCAPE_PROFILE_DIR"] = TEMPPATHS.named_dir("inkscape_profile", stem="automation")
        except Exception:
            pass
        try:
            # GTK writes recently-used.xbel under XDG_DATA_HOME; isolate it for automation runs.
            env["XDG_DATA_HOME"] = TEMPPATHS.named_dir("xdg_data", stem="automation")
        except Exception:
            pass
    return env


def run(
    argv: list[str],
    *,
    exe_dir: str | None = None,
    env: dict[str, str] | None = None,
    on_output=None,
) -> tuple[int, str]:
    try:
        _l.i("[inkscape_cli] run cwd='%s' argv=%s", exe_dir or "", " ".join(str(part) for part in (argv or [])))
    except Exception:
        pass
    kwargs = {
        "args": argv,
        "cwd": exe_dir or None,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_output is None:
        proc = subprocess.run(**kwargs)
        msg = (proc.stderr or proc.stdout or "").strip()
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
) -> tuple[int, str]:
    script = [str(cmd or "").strip() for cmd in (commands or []) if str(cmd or "").strip()]
    if not script:
        return 0, ""
    payload = "\n".join(script + ["quit"]) + "\n"
    _l.i("[inkscape_shell] start exe='%s' cwd='%s' commands=%d", exe, exe_dir or "", len(script))
    for idx, cmd in enumerate(script, start=1):
        _l.i("[inkscape_shell] cmd%02d %s", idx, cmd)
    kwargs = {
        "args": [exe, "--shell"],
        "cwd": exe_dir or None,
        "env": env,
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
    while proc.poll() is None or any(reader.is_alive() for reader in readers) or not output_q.empty():
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
    for reader in readers:
        try:
            reader.join(timeout=0.2)
        except Exception:
            pass
    rc = int(proc.returncode or 0)
    msg = "".join(chunks).strip()
    if rc == 0:
        _l.i("[inkscape_shell] ok rc=%d", int(proc.returncode))
    else:
        _l.w("[inkscape_shell] failed rc=%d msg=%s", rc, msg[:1000])
    return rc, msg


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
