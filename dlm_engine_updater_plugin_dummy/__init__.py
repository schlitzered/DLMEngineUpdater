import logging
from typing import Any

from dlm_engine_updater.plugin import DlmEnginePluginBase
from dlm_engine_updater.plugin import DlmEnginePluginError


class DlmEnginePlugin(DlmEnginePluginBase):
    def init(self):
        self.log.info("Initializing plugin")

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
        level = logging.getLevelName(level)
        print(f"PRE {level} {phase} {msg}")

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
        # level = logging.getLevelName(level)
        # print(f"POST {level} {phase} {msg}")
        pass

    def phase_pre_hook(self, phase: str, **kwargs: Any):
        self.log.info(f"dummy plugin phase_pre_hook {phase}", phase=phase)
        return True

    def phase_post_hook(self, phase: str, **kwargs: Any):
        self.log.info(f"dummy plugin phase_post_hook {phase}", phase=phase)
        return True
