import calendar
import datetime
import subprocess
import os
import pwd
import random
import stat
import sys
import time

from pep3143daemon import PidFile

from dlm_engine_updater.config import DlmUpdaterConfig
from dlm_engine_updater.lock import DlmEngineLock
from dlm_engine_updater.logger import DlmLogger
from dlm_engine_updater.plugin import DlmEnginePluginManager
from dlm_engine_updater.plugin import PluginHookType
from dlm_engine_updater.plugin import PluginTiming


class DlmEngineUpdater:
    def __init__(self, cfg, after_reboot, date_constraint, random_sleep):
        self._config = DlmUpdaterConfig(_env_file=cfg)
        self._plugin_manager = DlmEnginePluginManager(self._config)
        self._log = DlmLogger(self._config, plugin_manager=self._plugin_manager)
        self._plugin_manager.log = self._log
        self._plugin_manager.init()
        self._after_reboot = after_reboot
        self._date_constraints = None
        self._random_sleep = random_sleep
        self._lock = PidFile(f"{self.config.main.basedir}/lock")
        self.date_constraints = date_constraint
        self._dlm_lock = DlmEngineLock(
            log=self.log,
            ca=self.config.main.api.ca,
            endpoint=self.config.main.api.endpoint,
            secret=self.config.main.api.secret,
            secret_id=self.config.main.api.secretid,
            lock_name=self.config.main.api.lockname,
            wait=self.config.main.wait,
            wait_max=self._config.main.waitmax,
            noop=self.config.main.api.noop,
        )
        self._dlm_lock_acquired = False
        self._user_scripts_users = None
        self._user_root = None

    @property
    def log(self) -> DlmLogger:
        return self._log

    @property
    def plugin_manager(self) -> DlmEnginePluginManager:
        return self._plugin_manager

    @property
    def config(self):
        return self._config

    @property
    def date_constraints(self):
        return self._date_constraints

    @date_constraints.setter
    def date_constraints(self, constraints):
        if not constraints:
            return
        constraints = constraints.split(",")
        _constraints = list()
        for constraint in constraints:
            self.log.info(f"parsing date constraint: {constraint}")
            try:
                nth, day = constraint.split(":", maxsplit=1)
            except ValueError:
                self.log.fatal("Invalid date constraint, must match NUM:DAY_ABBR")
                sys.exit(1)
            try:
                nth = int(nth)
            except ValueError:
                self.log.fatal(
                    "Invalid date constraint, number must be between 1 and 4"
                )
                sys.exit(1)
            if nth not in range(1, 5):
                self.log.fatal(
                    "Invalid date constraint, number must be between 1 and 4"
                )
                sys.exit(1)
            if day not in calendar.day_abbr:
                self.log.fatal(
                    f"Invalid date constraint, day must be one of {list(calendar.day_abbr)}"
                )
                sys.exit(1)
            _constraints.append({"nth": nth, "day": day})
        self._date_constraints = _constraints

    @property
    def dlm_lock(self):
        return self._dlm_lock

    @property
    def dlm_lock_acquired(self):
        return self._dlm_lock_acquired

    @dlm_lock_acquired.setter
    def dlm_lock_acquired(self, value):
        self._dlm_lock_acquired = value

    @property
    def lock(self):
        return self._lock

    @property
    def after_reboot(self):
        return self._after_reboot

    @property
    def user_script_users(self):
        if self._user_scripts_users is None:
            self._user_scripts_users = list()
            if not self.config.main.userscriptusers:
                return self._user_scripts_users
            for user in self.config.main.userscriptusers:
                try:
                    self._user_scripts_users.append(pwd.getpwnam(user))
                except KeyError:
                    self.log.warning(f"user {user} does not exist")
                    continue
        return self._user_scripts_users

    @property
    def user_root(self):
        if not self._user_root:
            _user = pwd.getpwuid(os.getuid()).pw_name
            if _user != "root":
                self.log.warning(
                    "running as non-root user, some features may be limited"
                )
            self._user_root = pwd.getpwnam(_user)
        return self._user_root

    def random_sleep(self):
        sleep = random.randint(0, self._random_sleep)
        self.log.info(f"sleeping {sleep} seconds")
        time.sleep(sleep)
        self.log.info(f"sleeping {sleep} seconds,done ")

    def execute_shell(self, args, user, phase, script, env=None):
        if not env:
            env = {}
        env["DLM_ENGINE_UPDATER_LOCK_NAME"] = self.dlm_lock.lock_name
        env["DLM_ENGINE_UPDATER_PHASE"] = self.task
        env.setdefault(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )
        env.setdefault("USER", user)
        env.setdefault("LOGNAME", user)
        pwent = pwd.getpwnam(user)
        env.setdefault("HOME", pwent.pw_dir)
        if user != "root":
            args = ["sudo", "-n", "-E", "-u", user] + args
        p = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        for line in p.stdout:
            self.log.info(line.rstrip(), phase=phase, script=script)
        p.stdout.close()
        p_wait = p.wait()
        self.log.info("subprocess finished", phase=phase, script=script, return_code=p_wait)
        return p_wait

    @property
    def task(self):
        try:
            with open(f"{self.config.main.basedir}/state", "r") as state:
                return state.readline().rstrip("\n")
        except FileNotFoundError:
            return "needs_update"

    @task.deleter
    def task(self):
        try:
            os.remove(f"{self.config.main.basedir}/state")
        except OSError as err:
            self.log.error(f"could not remove state file: {err}")

    @task.setter
    def task(self, task):
        self.log.info(f"setting task to {task}")
        try:
            with open(f"{self.config.main.basedir}/state", "w") as state:
                state.write(f"{task}\n")
        except OSError as err:
            self.log.fatal(f"could not set state: {err}")
            sys.exit(1)

    def check_date_constraints(self):
        if not self.date_constraints:
            self.log.info("no date constraint set")
            return None
        self.log.info("checking date constraints")
        for constraint in self.date_constraints:
            if self._check_date_constraint(
                nth=constraint["nth"],
                day=constraint["day"],
            ):
                return True
        self.log.warning("no date constraint matched")
        sys.exit(0)

    def _check_date_constraint(self, nth, day):
        self.log.info(f"checking constraint {nth}:{day}")
        now = datetime.datetime.now()
        if now.strftime("%a") != day:
            self.log.info(f"today is not {day}")
            return False

        nth_count = 0
        current_date = datetime.datetime(year=now.year, month=now.month, day=1)
        while current_date.month == now.month and current_date <= now:
            if current_date.strftime("%a") == day:
                nth_count += 1
            current_date += datetime.timedelta(days=1)

        if nth_count != nth:
            self.log.info(f"today is not the {nth}. {day}")
            return False
        self.log.info(f"today is the {nth}. {day}, running dlm_engine_updater")
        return True

    def check_reboot(self):
        if self.after_reboot:
            if self.task != "post_update":
                self.log.info("reboot was not triggered by dlm_engine_updater, exiting")
                sys.exit(0)
            else:
                self.log.info(
                    "reboot was triggered by dlm_engine_updater, picking up remaining tasks"
                )

    def dlm_lock_get(self):
        self.dlm_lock.acquire()
        self.dlm_lock_acquired = True
        self.do_ext_notify(phase="main", script="none", return_code=0)
        self.task = "pre_update"

    def dlm_lock_release(self):
        self.log.info("releasing lock")
        self.dlm_lock.release()
        self.dlm_lock_acquired = False
        self.do_ext_notify(
            phase="main", script="none", return_code=0, updater_running=False
        )
        del self.task
        sys.exit(0)

    def do_ext_notify(self, phase, script, return_code, updater_running=True):
        files = self.get_scripts("ext_notify.d", phase=phase)
        for _file, _user in files:
            self.log.info(f"running ext notify script: {_file}")
            self.execute_shell(
                [
                    _file,
                    self.dlm_lock.lock_name,
                    str(self.dlm_lock_acquired),
                    str(updater_running),
                    phase,
                    script,
                    str(return_code),
                ],
                user=_user,
                phase=phase,
                script=script,
            )

    def on_failure(self, phase, script, return_code, updater_running=True):
        files = self.get_scripts("on_failure.d", phase=phase)
        for _file, _user in files:
            self.log.info(f"running on failure script: {_file}", phase=phase)
            self.execute_shell(
                [
                    _file,
                    self.dlm_lock.lock_name,
                    str(self.dlm_lock_acquired),
                    str(updater_running),
                    phase,
                    script,
                    str(return_code),
                ],
                user=_user,
                phase=phase,
                script=None,
            )

    def get_scripts(self, path, phase, skip_user_scripts=True):
        _path = f"{self.config.main.basedir}/{path}"
        scripts = list()
        for script in self._get_scripts(_path, self.user_root, phase=phase):
            scripts.append([script, self.user_root.pw_name])

        if self.user_script_users and not skip_user_scripts:
            for user in self.user_script_users:
                _path = os.path.join(user.pw_dir, "dlm_engine_updater", path)
                for script in self._get_scripts(_path, user, phase=phase):
                    scripts.append([script, user.pw_name])

        scripts.sort(key=lambda x: os.path.basename(x[0]))
        return scripts

    def _get_scripts(self, path, user, phase):
        user_name = user.pw_name

        files = list()
        try:
            candidates = os.listdir(path)
        except FileNotFoundError as err:
            return files
        for _file in candidates:
            _file = os.path.join(path, _file)
            self.log.debug(f"found the file: {_file}", phase=phase)
            if not os.path.isfile(_file):
                continue
            if not os.stat(_file).st_uid == user.pw_uid:
                self.log.warning(f"file not owned by {user_name}", phase=phase)
                continue
            if not (os.stat(_file).st_mode & stat.S_IXUSR):
                self.log.warning(f"file not executable by {user_name}", phase=phase)
                continue
            if os.stat(_file).st_mode & stat.S_IWOTH:
                self.log.warning("file world writeable", phase=phase)
                continue
            if os.stat(_file).st_mode & stat.S_IWGRP:
                self.log.warning("file group writeable", phase=phase)
                continue
            files.append(_file)
        return files

    def needs_update(self):
        update = False
        self.log.info("checking if updates are available", phase="needs_update")
        files = self.get_scripts("needs_update.d", phase="needs_update")
        for _file, _user in files:
            self.log.info(f"running: {_file}", phase="needs_update")
            return_code = self.execute_shell(
                [_file], user=_user, phase="needs_update", script=_file
            )
            if return_code != 0:
                update = True
            self.do_ext_notify(
                phase="needs_update", script=_file, return_code=return_code
            )
            self.log.info(f"running: {_file} done", phase="needs_update")
            if update:
                self.log.info("updates are available", phase="needs_update")
                break

        if update:
            self.task = "lock_get"
        else:
            self.log.info("no updates available", phase="needs_update")
            self.do_ext_notify(
                phase="main", script="none", return_code=0, updater_running=False
            )
            sys.exit(0)

    def update(self):
        self.log.info("running_update scripts", phase="update")
        files = self.get_scripts("update.d", phase="update")
        for _file, _user in files:
            self.log.info(f"running: {_file}")
            return_code = self.execute_shell(
                [_file], user=_user, phase="update", script=_file
            )
            if return_code != 0:
                self.log.info("script failed, stopping, keeping lock", phase="update")
                self.on_failure(phase="update", script=_file, return_code=return_code)
                sys.exit(1)
            self.do_ext_notify(phase="update", script=_file, return_code=return_code)
            self.log.info(f"running: {_file} done", phase="update")
        self.task = "needs_reboot"

    def post_update(self):
        self.log.info("running post_update scripts", phase="post_update")
        self.dlm_lock_acquired = True
        if not self.plugin_manager.run(
            hook_type=PluginHookType.PHASE,
            timing=PluginTiming.PRE,
            phase="post_update",
        ):
            self.log.info("post_update plugin failed, stopping", phase="post_update")
            sys.exit(1)
        files = self.get_scripts(
            "post_update.d", skip_user_scripts=False, phase="post_update"
        )
        for _file, _user in files:
            self.log.info(f"running: {_file}", phase="post_update")
            return_code = self.execute_shell(
                [_file], user=_user, phase="post_update", script=_file
            )
            if return_code != 0:
                self.log.info(
                    "script failed, stopping, keeping lock", phase="post_update"
                )
                self.on_failure(
                    phase="post_update", script=_file, return_code=return_code
                )
                sys.exit(1)
            self.do_ext_notify(
                phase="post_update", script=_file, return_code=return_code
            )
            self.log.info(f"running: {_file} done", phase="post_update")
        if not self.plugin_manager.run(
            hook_type=PluginHookType.PHASE,
            timing=PluginTiming.POST,
            phase="post_update",
        ):
            self.log.info("post_update plugin failed, stopping", phase="post_update")
            sys.exit(1)
        self.task = "lock_release"

    def pre_update(self):
        self.log.info("running pre_update scripts", phase="pre_update")
        if not self.plugin_manager.run(
            hook_type=PluginHookType.PHASE,
            timing=PluginTiming.PRE,
            phase="pre_update",
        ):
            self.log.info("pre_update plugin failed, stopping", phase="pre_update")
            sys.exit(1)
        files = self.get_scripts(
            "pre_update.d", skip_user_scripts=False, phase="pre_update"
        )
        for _file, _user in files:
            self.log.info(f"running: {_file}")
            return_code = self.execute_shell(
                [_file], user=_user, phase="pre_update", script=_file
            )
            if return_code != 0:
                self.log.info(
                    "script failed, stopping, keeping lock", phase="pre_update"
                )
                self.on_failure(
                    phase="pre_update", script=_file, return_code=return_code
                )
                sys.exit(1)
            self.do_ext_notify(
                phase="pre_update", script=_file, return_code=return_code
            )
            self.log.info(f"running: {_file} done", phase="pre_update")
        if not self.plugin_manager.run(
            hook_type=PluginHookType.PHASE,
            timing=PluginTiming.POST,
            phase="pre_update",
        ):
            self.log.info("pre_update plugin failed, stopping", phase="pre_update")
            sys.exit(1)
        self.task = "update"

    def reboot(self):
        self.log.info("rebooting", phase="reboot")
        for _file, _user in self.get_scripts("reboot.d", phase="reboot"):
            self.log.info(f"running: {_file}", phase="reboot")
            return_code = self.execute_shell(
                [_file], user=_user, phase="reboot", script=_file
            )
            if return_code != 0:
                self.log.info("script failed, stopping, keeping lock", phase="reboot")
                self.on_failure(phase="reboot", script=_file, return_code=return_code)
                sys.exit(1)
            self.do_ext_notify(phase="reboot", script=_file, return_code=return_code)
            self.log.info(f"running: {_file} done", phase="reboot")
        self.task = "post_update"
        sys.exit(0)

    def needs_reboot(self):
        self.log.info("running needs reboot scripts", phase="needs_reboot")
        files = self.get_scripts("needs_reboot.d", phase="needs_reboot")
        if not files:
            self.log.info(
                "no needs_reboot scripts found, defaulting to reboot",
                phase="needs_reboot",
            )
            self.task = "reboot"
            return
        reboot = False
        for _file, _user in files:
            self.log.info(f"running: {_file}", phase="needs_reboot", script=_file)
            return_code = self.execute_shell(
                [_file], user=_user, phase="needs_reboot", script=_file
            )
            if return_code != 0:
                self.log.info(f"running: {_file} done", phase="needs_reboot")
                self.do_ext_notify(
                    phase="needs_reboot", script=_file, return_code=return_code
                )
                reboot = True
                break
            self.do_ext_notify(
                phase="needs_reboot", script=_file, return_code=return_code
            )
            self.log.info(f"running: {_file} done", phase="needs_reboot")
        if reboot:
            self.task = "reboot"
        else:
            self.task = "post_update"

    def work(self):
        self.log.info(f"running dlm engine updater as {self.user_root.pw_name}")
        self.check_date_constraints()
        self.lock.acquire()
        self.check_reboot()
        self.random_sleep()
        while True:
            task = self.task
            if task == "needs_update":
                self.needs_update()
            elif task == "lock_get":
                self.dlm_lock_get()
            elif task == "lock_release":
                self.dlm_lock_release()
            elif task == "pre_update":
                self.pre_update()
            elif task == "update":
                self.update()
            elif task == "needs_reboot":
                self.needs_reboot()
            elif task == "reboot":
                self.reboot()
            elif task == "post_update":
                self.post_update()
            else:
                self.log.fatal(f"found garbage in status file: {self.task}")
                del self.task
                sys.exit(1)
