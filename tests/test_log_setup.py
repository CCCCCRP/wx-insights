from pathlib import Path
from unittest.mock import patch

from worker.log_setup import setup_logging


def test_setup_logging_writes_file(tmp_path: Path):
    log_file = tmp_path / "worker.log"
    with patch("worker.log_setup._CONFIGURED", False):
        with patch("worker.log_setup.LOG_CONSOLE", False):
            with patch("worker.log_setup.LOG_SPLIT", False):
                with patch("worker.log_setup.LOG_AGGREGATE", True):
                    with patch("worker.log_setup.LOG_FILE", log_file):
                        with patch("worker.log_setup.LOG_DIR", tmp_path):
                            path = setup_logging()
    assert path == log_file
    assert log_file.exists()

    import logging
    logging.getLogger("worker.test").info("hello log")
    assert "hello log" in log_file.read_text(encoding="utf-8")


def test_split_logging_routes_by_module(tmp_path: Path):
    log_file = tmp_path / "worker.log"
    with patch("worker.log_setup._CONFIGURED", False):
        with patch("worker.log_setup.LOG_CONSOLE", False):
            with patch("worker.log_setup.LOG_SPLIT", True):
                with patch("worker.log_setup.LOG_AGGREGATE", False):
                    with patch("worker.log_setup.LOG_FILE", log_file):
                        with patch("worker.log_setup.LOG_DIR", tmp_path):
                            setup_logging()

    import logging
    logging.getLogger("worker.crawl.service").info("crawl msg")
    logging.getLogger("worker.insight.service").info("insight msg")
    logging.getLogger("worker.cli").info("cli msg")

    crawl_log = (tmp_path / "crawl.log").read_text(encoding="utf-8")
    insight_log = (tmp_path / "insight.log").read_text(encoding="utf-8")
    worker_log = log_file.read_text(encoding="utf-8")

    assert "crawl msg" in crawl_log
    assert "crawl msg" not in insight_log
    assert "insight msg" in insight_log
    assert "insight msg" not in crawl_log
    assert "cli msg" in worker_log
    assert "crawl msg" not in worker_log


def test_aggregate_includes_all_modules(tmp_path: Path):
    log_file = tmp_path / "worker.log"
    with patch("worker.log_setup._CONFIGURED", False):
        with patch("worker.log_setup.LOG_CONSOLE", False):
            with patch("worker.log_setup.LOG_SPLIT", True):
                with patch("worker.log_setup.LOG_AGGREGATE", True):
                    with patch("worker.log_setup.LOG_FILE", log_file):
                        with patch("worker.log_setup.LOG_DIR", tmp_path):
                            setup_logging()

    import logging
    logging.getLogger("worker.crawl.service").info("crawl agg")
    logging.getLogger("worker.insight.service").info("insight agg")

    worker_log = log_file.read_text(encoding="utf-8")
    assert "crawl agg" in worker_log
    assert "insight agg" in worker_log
    assert "crawl agg" in (tmp_path / "crawl.log").read_text(encoding="utf-8")
