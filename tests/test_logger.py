import logging
import time
from logging.handlers import TimedRotatingFileHandler
from unittest import mock

import pytest

from dlm_engine_updater.logger import DlmLogger
from dlm_engine_updater.plugin import PluginTiming


class _SN:
    """Minimal attribute-only object (like types.SimpleNamespace)."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def make_config(tmp_path):
    return _SN(
        main=_SN(
            log=_SN(level="DEBUG", file=str(tmp_path / "test.log"), retention=1),
        ),
    )


@pytest.fixture(autouse=True)
def close_handlers():
    # DlmLogger configures the global "application" logger, close its file
    # handlers after each test so they do not leak into other tests
    yield
    app_log = logging.getLogger("application")
    for handler in app_log.handlers[:]:
        handler.close()
        app_log.removeHandler(handler)


@pytest.fixture
def plugin_manager():
    return mock.Mock()


@pytest.fixture
def logger(tmp_path, plugin_manager):
    return DlmLogger(config=make_config(tmp_path), plugin_manager=plugin_manager)


@pytest.fixture
def internal_log(logger):
    with mock.patch.object(logger, "_log") as m:
        yield m


class TestLevels:
    @pytest.mark.parametrize(
        "method_name, expected_level",
        [
            ("critical", logging.CRITICAL),
            ("debug", logging.DEBUG),
            ("error", logging.ERROR),
            ("fatal", logging.FATAL),
            ("info", logging.INFO),
            ("warning", logging.WARNING),
        ],
    )
    def test_method_passes_level_constant(
        self, logger, internal_log, method_name, expected_level
    ):
        getattr(logger, method_name)("message")
        internal_log.log.assert_called_once_with(expected_level, "message")

    def test_fatal_writes_to_log_file(self, tmp_path, logger):
        # uses the real logging machinery, logging.fatal (a function) instead
        # of logging.FATAL used to raise "TypeError: level must be an integer"
        logger.fatal("fatal message")
        content = (tmp_path / "test.log").read_text()
        assert "UTC - CRITICAL - MainThread - fatal message" in content


class TestSetup:
    def test_file_handler(self, tmp_path, logger):
        assert len(logger._log.handlers) == 1
        handler = logger._log.handlers[0]
        assert isinstance(handler, TimedRotatingFileHandler)
        assert handler.baseFilename == str(tmp_path / "test.log")
        assert handler.formatter._fmt == (
            "%(asctime)sUTC - %(levelname)s - %(threadName)s - %(message)s"
        )
        assert handler.formatter.converter is time.gmtime
        assert logger._log.level == logging.DEBUG

    def test_new_instance_replaces_and_closes_handlers(self, tmp_path, plugin_manager):
        config = make_config(tmp_path)
        first = DlmLogger(config=config, plugin_manager=plugin_manager)
        old_handler = first._log.handlers[0]
        second = DlmLogger(config=config, plugin_manager=plugin_manager)
        assert len(second._log.handlers) == 1
        assert second._log.handlers[0] is not old_handler
        # FileHandler.close() drops the stream
        assert old_handler.stream is None


class TestPluginHooks:
    def test_hooks_run_before_and_after_log(self, logger, internal_log, plugin_manager):
        calls = mock.Mock()
        calls.attach_mock(plugin_manager.run, "run")
        calls.attach_mock(internal_log.log, "log")
        logger.info("message", phase="lock_get", script="s", return_code=0)
        hook_kwargs = dict(
            level=logging.INFO,
            msg="message",
            phase="lock_get",
            script="s",
            return_code=0,
        )
        assert [c[0] for c in calls.mock_calls] == ["run", "log", "run"]
        assert calls.mock_calls[0].kwargs == dict(
            hook_type=mock.ANY, timing=PluginTiming.PRE, **hook_kwargs
        )
        assert calls.mock_calls[2].kwargs == dict(
            hook_type=mock.ANY, timing=PluginTiming.POST, **hook_kwargs
        )

    def test_plugin_manager_error_does_not_suppress_log(
        self, logger, internal_log, plugin_manager
    ):
        # the plugin manager already catches errors of individual plugins,
        # this guards against errors in the plugin manager itself
        plugin_manager.run.side_effect = RuntimeError("plugin manager error")
        logger.info("message")
        internal_log.log.assert_called_once_with(logging.INFO, "message")
        assert plugin_manager.run.call_count == 2

    def test_post_hook_runs_if_log_raises(self, logger, internal_log, plugin_manager):
        internal_log.log.side_effect = OSError("disk full")
        with pytest.raises(OSError, match="disk full"):
            logger.info("message")
        assert plugin_manager.run.call_count == 2
        assert plugin_manager.run.call_args.kwargs["timing"] == PluginTiming.POST
