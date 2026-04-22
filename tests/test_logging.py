"""Tests for logging setup module."""

import logging
from pathlib import Path

from gamemode.logging_setup import setup_logging


class TestLogging:
    def test_setup_creates_handlers(self, tmp_path_cfg, logger):
        log = setup_logging(tmp_path_cfg, to_file=False, debug=False)
        assert len(log.handlers) >= 1

    def test_file_handler(self, tmp_path_cfg):
        log = setup_logging(tmp_path_cfg, to_file=True)
        file_handlers = [h for h in log.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        assert Path(file_handlers[0].baseFilename) == tmp_path_cfg.log_file
