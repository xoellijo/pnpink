# -*- coding: utf-8 -*-
"""Small, shared wrapper for launching Inkscape CLI safely."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import log as LOG

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


def clean_launch_env() -> dict[str, str]:
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
    return env


def run(argv: list[str], *, exe_dir: str | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
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
    proc = subprocess.run(**kwargs)
    msg = (proc.stderr or proc.stdout or "").strip()
    return int(proc.returncode), msg


def run_shell_commands(
    exe: str,
    commands: list[str],
    *,
    exe_dir: str | None = None,
    env: dict[str, str] | None = None,
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
    proc = subprocess.run(input=payload, **kwargs)
    msg = (proc.stderr or proc.stdout or "").strip()
    if proc.returncode == 0:
        _l.i("[inkscape_shell] ok rc=%d", int(proc.returncode))
    else:
        _l.w("[inkscape_shell] failed rc=%d msg=%s", int(proc.returncode), msg[:1000])
    return int(proc.returncode), msg


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


def build_png_export_argv(exe: str, svg_path: str, png_path: str, *, page_selector: str | None = None, dpi: int = 300) -> list[str]:
    argv = [
        exe,
        svg_path,
        "--export-type=png",
        f"--export-filename={png_path}",
        f"--export-dpi={int(dpi)}",
    ]
    if page_selector:
        argv.append(f"--export-page={page_selector}")
    return argv


def build_export_id_png_argv(exe: str, svg_path: str, node_id: str, png_path: str, *, dpi: int) -> list[str]:
    return [
        exe,
        svg_path,
        f"--export-id={node_id}",
        "--export-id-only",
        f"--export-dpi={int(dpi)}",
        f"--export-filename={png_path}",
    ]


def build_shell_pdf_commands(
    svg_path: str,
    pdf_path: str,
    *,
    dpi: int = 300,
    ignore_filters: bool = False,
) -> list[str]:
    return [
        f"file-open:{svg_path}",
        f"export-ignore-filters:{'true' if ignore_filters else 'false'}",
        f"export-filename:{pdf_path}",
        "export-type:pdf",
        f"export-dpi:{int(dpi)}",
        "export-do",
    ]


def build_shell_png_commands(
    svg_path: str,
    png_path: str,
    *,
    dpi: int = 300,
) -> list[str]:
    return [
        f"file-open:{svg_path}",
        f"export-filename:{png_path}",
        "export-type:png",
        f"export-dpi:{int(dpi)}",
        "export-do",
    ]


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


def build_shell_export_id_png_commands(
    svg_path: str,
    exports: list[tuple[str, str, int]],
) -> list[str]:
    commands = [f"file-open:{svg_path}"]
    for node_id, png_path, dpi in exports:
        commands.extend(
            [
                f"export-id:{node_id}",
                "export-id-only:true",
                f"export-filename:{png_path}",
                "export-type:png",
                f"export-dpi:{int(dpi)}",
                "export-do",
            ]
        )
    return commands
