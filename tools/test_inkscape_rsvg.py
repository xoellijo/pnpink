from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_suffix(".log")
    lines: list[str] = []
    lines.append(f"executable={sys.executable}")
    lines.append(f"version={sys.version}")
    lines.append(f"cwd={os.getcwd()}")
    try:
        import gi

        gi.require_version("Rsvg", "2.0")
        from gi.repository import Gio, GLib, Rsvg

        lines.append("gi=ok")
        lines.append(f"Rsvg={Rsvg}")
        lines.append(f"has_handle={hasattr(Rsvg, 'Handle')}")
        lines.append(f"has_rectangle={hasattr(Rsvg, 'Rectangle')}")
        lines.append(f"has_keep_image_data={hasattr(getattr(Rsvg, 'HandleFlags', None), 'FLAG_KEEP_IMAGE_DATA')}")

        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect x="0" y="0" width="10" height="10" fill="red"/></svg>'
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(svg))
        handle = Rsvg.Handle.new_from_stream_sync(stream, None, Rsvg.HandleFlags.FLAGS_NONE, None)
        lines.append("handle=ok")
        lines.append(f"handle_type={type(handle)}")
    except Exception as ex:
        lines.append(f"ERROR={type(ex).__name__}: {ex}")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
