from enum import Enum
import importlib
import sys
from typing import Any

from dlm_engine_updater.config import DlmUpdaterConfig
from dlm_engine_updater.config import DlmUpdaterConfigMainPlugin


class PluginHookType(Enum):
    LOGGER = "logger"
    PHASE = "phase"


class PluginTiming(Enum):
    PRE = "pre"
    POST = "post"


class DlmEnginePluginError(Exception):
    pass


class DlmEnginePluginBase:
    def __init__(
        self,
        config: DlmUpdaterConfigMainPlugin,
    ):
        self._config = config
        self._log = None

    @property
    def log(self):
        return self._log

    @log.setter
    def log(self, log):
        self._log = log

    def init(self):
        pass

    def logger_pre_hook(
        self,
        level: str,
        msg: str,
        phase=None,
        script=None,
        return_code=None,
        **kwargs: Any,
    ):
        # do not use self.log here, it will cause a recursion loop
        pass

    def logger_post_hook(
        self,
        level: str,
        msg: str,
        phase=None,
        script=None,
        return_code=None,
        **kwargs: Any,
    ):
        # do not use self.log here, it will cause a recursion loop
        pass

    def phase_pre_hook(
        self,
        phase: str,
        **kwargs: Any,
    ) -> bool:
        return True

    def phase_post_hook(
        self,
        phase: str,
        **kwargs: Any,
    ) -> bool:
        return True


class DlmEnginePluginManager:
    def __init__(
        self,
        config: DlmUpdaterConfig,
    ):
        self._config = config
        self._log = None
        self._plugins = dict()
        self._init()

    @property
    def config(self):
        return self._config

    @property
    def log(self):
        return self._log

    @log.setter
    def log(self, log):
        self._log = log
        for plugin in self._plugins.values():
            plugin.log = log

    @property
    def plugins(self):
        return self._plugins

    def _init(self):
        if not self.config.plugin:
            return
        for plugin_name, plugin_config in self.config.plugin.items():
            self._init_import(plugin_name, plugin_config)

    def _init_import(self, plugin_name, plugin_config):
        module_name = f"dlm_engine_updater_plugin_{plugin_name}"
        try:
            module = importlib.import_module(module_name)
            plugin = module.DlmEnginePlugin(plugin_config)
            self._plugins[plugin_name] = plugin
        except ImportError as err:
            print(f"Failed to import module '{module_name}': {err}")
            sys.exit(1)
        return None

    def init(self):
        for plugin_name, plugin in self._plugins.items():
            self.log.info(f"Initializing plugin: {plugin_name}")
            plugin.init()
            self.log.info(f"Plugin {plugin_name} initialized")

    def run(
        self,
        hook_type: PluginHookType,
        timing: PluginTiming,
        phase: str = None,
        **kwargs
    ):
        if hook_type == PluginHookType.LOGGER:
            return self._run_logger_hooks(timing=timing, phase=phase, **kwargs)
        elif hook_type == PluginHookType.PHASE:
            return self._run_phase_hooks(timing=timing, phase=phase, **kwargs)
        return None

    def _run_logger_hooks(
        self,
        timing: PluginTiming,
        level: str,
        msg: str,
        phase=None,
        script=None,
        return_code=None,
        **kwargs,
    ):
        for plugin_name, plugin in self._plugins.items():
            try:
                if timing == PluginTiming.PRE:
                    plugin.logger_pre_hook(
                        level=level,
                        msg=msg,
                        phase=phase,
                        script=script,
                        return_code=return_code,
                        **kwargs,
                    )
                else:
                    plugin.logger_post_hook(
                        level=level,
                        msg=msg,
                        phase=phase,
                        script=script,
                        return_code=return_code,
                        **kwargs,
                    )
            except Exception:
                pass

    def _run_phase_hooks(self, timing: PluginTiming, phase: str, **kwargs):
        success = True
        for plugin_name, plugin in self._plugins.items():
            try:
                if timing == PluginTiming.PRE:
                    if not plugin.phase_pre_hook(phase, **kwargs):
                        self.log.warning(
                            f"Plugin {plugin_name} prevented phase {phase} execution"
                        )
                        success = False
                else:
                    if not plugin.phase_post_hook(phase, **kwargs):
                        self.log.warning(
                            f"Plugin {plugin_name} failed phase {phase} execution"
                        )
                        success = False
            except Exception as err:
                self.log.error(f"Error in plugin {plugin_name} phase_pre_hook: {err}")
        return success
