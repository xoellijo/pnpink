from pathlib import Path

import log


def test_logger_replaces_previous_execution_log(tmp_path: Path):
    target = tmp_path / "pnpink.log"
    target.write_text("previous execution\n", encoding="utf-8")

    logger = log.Logger("test", "none", "info", str(target))
    logger.info("current execution")
    logger.close()

    content = target.read_text(encoding="utf-8")
    assert "previous execution" not in content
    assert "current execution" in content
