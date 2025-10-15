import logging
from logging.handlers import TimedRotatingFileHandler
import time

from dlm_engine_updater.plugin import DlmEnginePluginManager
from dlm_engine_updater.plugin import PluginHookType
from dlm_engine_updater.plugin import PluginTiming


class DlmLogger:
    def __init__(
        self,
        config,
        plugin_manager,
    ):
        self._config = config
        self._log = logging.getLogger("application")
        self._plugin_manager = plugin_manager
        self._logging()

    @property
    def config(self):
        return self._config

    @property
    def plugin_manager(self) -> DlmEnginePluginManager:
        return self._plugin_manager

    def _logging(self):
        logfmt = logging.Formatter(
            "%(asctime)sUTC - %(levelname)s - %(threadName)s - %(message)s"
        )
        logfmt.converter = time.gmtime
        handlers = []
        aap_level = self.config.main.log.level
        log = self.config.main.log.file
        retention = self.config.main.log.retention
        handlers.append(TimedRotatingFileHandler(log, "d", 1, retention))

        for handler in handlers:
            handler.setFormatter(logfmt)
            self._log.addHandler(handler)
        self._log.setLevel(aap_level)

    def log(
        self,
        level,
        msg,
        phase="main",
        script=None,
        return_code=None,
    ):
        self.plugin_manager.run(
            hook_type=PluginHookType.LOGGER,
            timing=PluginTiming.PRE,
            level=level,
            msg=msg,
            phase=phase,
            script=script,
            return_code=return_code,
        )
        self._log.log(level, msg)
        self.plugin_manager.run(
            hook_type=PluginHookType.LOGGER,
            timing=PluginTiming.POST,
            level=level,
            msg=msg,
            phase=phase,
            script=script,
            return_code=return_code,
        )

    def critical(
        self,
        msg,
        phase="main",
        script=None,
        return_code=None,
    ):
        self.log(logging.CRITICAL, msg, phase, script, return_code)

    def debug(
        self,
        msg,
        phase="main",
        script=None,
        return_code=None,
    ):
        self.log(logging.DEBUG, msg, phase, script, return_code)

    def error(
        self,
        msg,
        phase="main",
        script=None,
        return_code=None,
    ):
        self.log(logging.ERROR, msg, phase, script, return_code)

    def fatal(
        self,
        msg,
        phase="main",
        script=None,
        return_code=None,
    ):
        self.log(logging.fatal, msg, phase, script, return_code)

    def info(
        self,
        msg,
        phase="main",
        script=None,
        return_code=None,
    ):
        self.log(logging.INFO, msg, phase, script, return_code)

    def warning(
        self,
        msg,
        phase="main",
        script=None,
        return_code=None,
    ):
        self.log(logging.WARNING, msg, phase, script, return_code)
