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

    def test_debug_mode_creates_file_handler(self, tmp_path_cfg):
        """setup_logging with debug=True should create a file handler."""
        log = setup_logging(tmp_path_cfg, to_file=True, debug=True)
        file_handlers = [h for h in log.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        # Debug mode should set console handler to DEBUG level
        console_handlers = [
            h
            for h in log.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(console_handlers) == 1
        assert console_handlers[0].level == logging.DEBUG
