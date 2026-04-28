"""Tests for runner module."""

import logging

import pytest

from gamemode.runner import Runner


class TestRunner:
    def test_resolve_existing(self, runner):
        assert runner.resolve("sh") is not None

    def test_resolve_missing(self, runner):
        assert runner.resolve("this_command_should_not_exist_xyz") is None

    def test_require_existing(self, runner, caplog):
        caplog.set_level(logging.ERROR)
        assert runner.require("sh") is True
        assert not caplog.records

    def test_require_missing(self, runner, caplog):
        caplog.set_level(logging.ERROR)
        result = runner.require("no_such_cmd_xyz", feature="test")
        assert result is False
        assert any("no_such_cmd_xyz" in r.message for r in caplog.records)

    def test_run_success(self, runner):
        result = runner.run(["echo", "-n", "hello"], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello"

    def test_run_file_not_found(self, runner):
        """Runner.run should raise FileNotFoundError for missing commands."""
        with pytest.raises(FileNotFoundError):
            runner.run(["/nonexistent/command"], capture_output=True, text=True)

    def test_pipe(self, logger):
        """Runner.pipe should pass input_data to stdin."""
        r = Runner(logger)
        result = r.pipe(["cat"], input_data="hello")
        assert result.stdout.strip() == "hello"


class TestCheckedCommandRunner:
    def test_run_or_none_when_available(self, logger, fake_runner):
        fake_runner.when_resolved("echo", "/usr/bin/echo")
        fake_runner.when_run(("echo", "-n", "hello"), stdout="hello")
        checked = fake_runner.make_checked_runner("echo", "test")
        assert checked.is_available is True
        result = checked.run_or_none(["echo", "-n", "hello"])
        assert result is not None
        assert result.stdout.strip() == "hello"

    def test_run_or_none_when_missing(self, logger, fake_runner):
        checked = fake_runner.make_checked_runner("no_such_cmd", "test")
        assert checked.is_available is False
        result = checked.run_or_none(["no_such_cmd"])
        assert result is None

    def test_run_or_none_error_logging(self, logger, fake_runner, caplog):
        """When command returns non-zero, debug log should be emitted."""
        fake_runner.when_resolved("bad_cmd", "/usr/bin/bad_cmd")
        fake_runner.when_run(("bad_cmd", "fail"), rc=1, stderr="error")
        caplog.set_level(logging.DEBUG)
        checked = fake_runner.make_checked_runner("bad_cmd", "test_feature")
        result = checked.run_or_none(["bad_cmd", "fail"])
        assert result is not None
        assert result.returncode == 1
        assert any("returned 1" in r.message for r in caplog.records)
