from pathlib import Path
Path(__file__).with_suffix(".log").write_text("ok\n", encoding="utf-8")
