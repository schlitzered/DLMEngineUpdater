import argparse
import calendar
import configparser
import datetime
import subprocess
import logging
import os
import random
import socket
import stat
import sys
import time
from logging.handlers import TimedRotatingFileHandler

from pep3143daemon import PidFile
import httpx


def main():
    parser = argparse.ArgumentParser(description="DLM Updater")

    parser.add_argument(
        "--cfg",
        dest="cfg",
        action="store",
        default="/etc/dlm_engine_updater/config.ini",
        help="Full path to configuration",
    )

    parser.add_argument(
        "--after_reboot",
        dest="rbt",
        action="store_true",
        default=False,
        help="has to be used from init systems, to indicate that the script was called while booting.",
    )

    parser.add_argument(
        "--date_constraint",
        dest="date_constraint",
        action="store",
        default=None,
        help="exit if date constraint is not fulfilled."
        "example value: 3:Friday"
        "would only run if this is the 3rd Friday of a month.",
    )

    parser.add_argument(
        "--random_sleep",
        dest="random_sleep",
        action="store",
        required=False,
        default=0,
        type=int,
        help="add random sleep before acutally doing something",
    )

    parsed_args = parser.parse_args()

    instance = DlmEngineUpdater(
        cfg=parsed_args.cfg,
        after_reboot=parsed_args.rbt,
        date_constraint=parsed_args.date_constraint,
        random_sleep=parsed_args.random_sleep,
    )
    instance.work()


class DlmEngineLock(object):
    def __init__(
        self, log, lock_name, ca, secret, secret_id, endpoint, wait, wait_max, org_id
    ):
        self._api_version = None
        self._ca = ca
        self._endpoint = endpoint
        self._lock_name = lock_name
        self._org_id = org_id
        self._secret = secret
        self._secret_id = secret_id
        self._dlm_api = None
        self._wait = wait
        self._wait_max = wait_max
        self.log = log

    @property
    def api_version(self):
        if not self._api_version:
            url = f"{self.endpoint}/versions"
            resp = self.dlm_api.get(
                url=url,
            )
            if resp.status_code == 404:
                self._api_version = "1"
            else:
                for version in resp.json()["versions"]:
                    if not self._api_version:
                        self._api_version = version["version"]
                    elif version["version"] > self._api_version:
                        self._api_version = version["version"]
        return self._api_version

    @property
    def ca(self):
        return self._ca

    @property
    def endpoint(self):
        if self._endpoint.endswith("/api/v1/"):
            return self._endpoint[:-8]
        return self._endpoint

    @property
    def org_id(self):
        return self._org_id

    @property
    def secret(self):
        return self._secret

    @property
    def secret_id(self):
        return self._secret_id

    @property
    def dlm_api(self):
        if not self._dlm_api:
            self._dlm_api = httpx.Client(
                verify=self.ca,
                headers={
                    "x-id": self.secret_id,
                    "x-secret-id": self.secret_id,
                    "x-secret": self.secret,
                },
            )
        return self._dlm_api

    @property
    def lock_name(self):
        return self._lock_name

    @property
    def payload_acquire(self):
        if self.api_version == "1":
            return {"data": {"acquired_by": socket.getfqdn()}}
        elif self.api_version == "2":
            return {"acquired_by": socket.getfqdn()}

    def payload_release(self):
        if self.api_version == "1":
            return {"data": {"acquired_by": socket.getfqdn()}}

    @property
    def wait(self):
        return self._wait

    @property
    def wait_max(self):
        return self._wait_max

    @property
    def lock_url(self):
        if self.api_version == "1":
            return f"{self.endpoint}/api/v1/locks/{self.lock_name}"
        elif self.api_version == "2":
            return f"{self.endpoint}/api/v2/orgs/{self.org_id}/locks/{self.lock_name}"
        else:
            self.log.fatal(f"unsupported api version: {self.api_version}")
            sys.exit(1)

    def acquire(self):
        # todo check if lock is already acquired
        self.log.debug(f"waiting is set to {self.wait}")
        self.log.debug(f"max wait time is set to {self.wait_max}")
        if self.wait:
            _waited = 0
            while True:
                if self._acquire():
                    return
                else:
                    if _waited > self.wait_max:
                        self.log.error("exceeded max wait time, quiting")
                        sys.exit(1)
                    _sleep = random.randint(10, 60)
                    _waited += _sleep + 2
                    self.log.error(f"sleeping {_sleep} seconds")
                    time.sleep(_sleep)
        else:
            if not self._acquire():
                self.log.error("quiting")
                sys.exit(1)

    def _acquire(self):
        self.log.info(f"trying to acquire: {self.lock_url}")
        if self._acquire_check():
            return True
        try:
            resp = self.dlm_api.post(
                json=self.payload_acquire,
                timeout=10.0,
                url=self.lock_url,
            )
            self.log.debug(f"http status_code is: {resp.status_code}")
            self.log.debug(f"http_response is {resp.json()}")
            if resp.status_code == 201:
                self.log.info("success acquiring lock")
                return True
            else:
                self.log.error(f"could not acquire lock: {resp.json()}")
                return False
        except httpx.HTTPError as err:
            self.log.error(f"request error, retrying: {err}")

    def _acquire_check(self):
        self.log.info("checking if lock has been acquired")
        resp = self.dlm_api.get(
            timeout=10.0,
            url=self.lock_url,
        )
        if not resp.status_code == 200:
            self.log.info("lock currently not present in the system")
            return
        if self.api_version == "1":
            if not resp.json()["data"]["acquired_by"] == socket.getfqdn():
                self.log.info(
                    f"lock is currently acquired by {resp.json()['data']['acquired_by']}"
                )
                return
        elif self.api_version == "2":
            if not resp.json()["acquired_by"] == socket.getfqdn():
                self.log.info(
                    f"lock is currently acquired by {resp.json()['acquired_by']}"
                )
                return

        self.log.info("lock has been already acquired by this instance")
        return True

    def release(self):
        self.log.info(f"trying to release: {self.lock_url}")
        retries = 10
        while retries > 0:
            try:
                resp = self.dlm_api.request(
                    method="DELETE",
                    json=self.payload_release(),
                    timeout=10.0,
                    url=self.lock_url,
                )
                self.log.debug(f"http status_code is: {resp.status_code}")
                self.log.debug(f"http_response is {resp.json()}")
                if resp.status_code == 200:
                    self.log.info("success releasing lock")
                    return
                else:
                    self.log.error(f"could not release lock: {resp.json()}")
                    sys.exit(1)
            except (httpx.HTTPError, httpx.ConnectError) as err:
                self.log.error(f"request error, retrying: {err}")
                retries -= 1
                time.sleep(5)
        self.log.fatal("could not release lock")
        sys.exit(1)


class DlmEngineUpdater(object):
    def __init__(self, cfg, after_reboot, date_constraint, random_sleep):
        self._config_file = cfg
        self._config = configparser.ConfigParser()
        self._config_dict = None
        self._after_reboot = after_reboot
        self._date_constraint = None
        self._random_sleep = random_sleep
        self.log = logging.getLogger("application")
        self.config.read_file(open(self._config_file))
        self._logging()
        self._lock = PidFile(self.config.get("main", "lock"))
        self.date_constraint = date_constraint
        self._dlm_lock = DlmEngineLock(
            log=self.log,
            ca=self.config.get("main", "ca", fallback=None),
            endpoint=self.config.get("main", "endpoint"),
            org_id=self.config.get("main", "org_id", fallback="DLMUpdater"),
            secret=self.config.get("main", "secret"),
            secret_id=self.config.get("main", "secret_id"),
            lock_name=self.config.get("main", "lock_name"),
            wait=self.config.getboolean("main", "wait", fallback=False),
            wait_max=self.config.getint("main", "wait_max", fallback=3600),
        )
        self._dlm_lock_acquired = False

    @property
    def config(self):
        return self._config

    @property
    def date_constraint(self):
        return self._date_constraint

    @date_constraint.setter
    def date_constraint(self, value):
        if not value:
            return
        try:
            nth, day = value.split(":", maxsplit=1)
        except ValueError:
            self.log.fatal("Invalid date constraint, must match NUM:DAY_ABBR")
            sys.exit(1)
        try:
            nth = int(nth)
        except ValueError:
            self.log.fatal("Invalid date constraint, number must be between 1 and 4")
            sys.exit(1)
        if nth not in range(1, 5):
            self.log.fatal("Invalid date constraint, number must be between 1 and 4")
            sys.exit(1)
        if day not in calendar.day_abbr:
            self.log.fatal(
                f"Invalid date constraint, day must be one of {list(calendar.day_abbr)}"
            )
            sys.exit(1)
        self._date_constraint = {"nth": nth, "day": day}

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

    def _logging(self):
        logfmt = logging.Formatter(
            "%(asctime)sUTC - %(levelname)s - %(threadName)s - %(message)s"
        )
        logfmt.converter = time.gmtime
        handlers = []
        aap_level = self.config.get("main", "log_level")
        log = self.config.get("main", "log")
        retention = self.config.getint("main", "log_retention")
        handlers.append(TimedRotatingFileHandler(log, "d", 1, retention))

        for handler in handlers:
            handler.setFormatter(logfmt)
            self.log.addHandler(handler)
        self.log.setLevel(aap_level)
        self.log.debug("logger is up")

    def random_sleep(self):
        sleep = random.randint(0, self._random_sleep)
        self.log.info(f"sleeping {sleep} seconds")
        time.sleep(sleep)
        self.log.info(f"sleeping {sleep} seconds,done ")

    def execute_shell(self, args):
        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        for line in p.stdout:
            self.log.info(line.rstrip())
        p.stdout.close()
        return p.wait()

    @property
    def task(self):
        try:
            with open(self.config.get("main", "state"), "r") as state:
                return state.readline().rstrip("\n")
        except FileNotFoundError:
            return "needs_update"

    @task.deleter
    def task(self):
        try:
            os.remove(self.config.get("main", "state"))
        except OSError as err:
            self.log.error(f"could not remove state file: {err}")

    @task.setter
    def task(self, task):
        self.log.info(f"setting task to {task}")
        try:
            with open(self.config.get("main", "state"), "w") as state:
                state.write(f"{task}\n")
        except OSError as err:
            self.log.fatal(f"could not set state: {err}")
            sys.exit(1)

    def check_date_constraint(self):
        if not self.date_constraint:
            self.log.info("no date constraint set")
            return
        self.log.info(
            f"date constraint set to {self.date_constraint['nth']}. {self.date_constraint['day']}"
        )
        now = datetime.datetime.now()
        month_start = datetime.datetime(year=now.year, month=now.month, day=1)
        delta = now - month_start
        if now.strftime("%a") != self.date_constraint["day"]:
            self.log.fatal(f"today is not {self.date_constraint['day']}")
            sys.exit(1)
        nth_count = 0
        for i in range(1, delta.days + 2):
            _day = datetime.datetime(year=now.year, month=now.month, day=i)
            if _day.strftime("%a") == self.date_constraint["day"]:
                nth_count += 1
        if nth_count != self.date_constraint["nth"]:
            self.log.fatal(
                f"today is not the {self.date_constraint['nth']}. {self.date_constraint['day']}"
            )
            sys.exit(1)
        self.log.fatal(
            f"today is the {self.date_constraint['nth']}. {self.date_constraint['day']}, running dlm_engine_updater"
        )
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
        files = self.get_scripts("ext_notify.d")
        for _file in files:
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
                ]
            )

    def on_failure(self, phase, script, return_code, updater_running=True):
        files = self.get_scripts("on_failure.d", fallback_path="on_failure.d")
        for _file in files:
            self.log.info(f"running on failure script: {_file}")
            self.execute_shell(
                [
                    _file,
                    self.dlm_lock.lock_name,
                    str(self.dlm_lock_acquired),
                    str(updater_running),
                    phase,
                    script,
                    str(return_code),
                ]
            )

    def get_scripts(self, path, fallback_path=None):
        if fallback_path:
            _path = self.config.get("main", path, fallback=fallback_path)
        else:
            _path = self.config.get("main", path)
        files = list()
        candidates = os.listdir(_path)
        candidates.sort()
        for _file in candidates:
            _file = os.path.join(_path, _file)
            self.log.debug(f"found the file: {_file}")
            if not os.path.isfile(_file):
                continue
            if not os.stat(_file).st_uid == 0:
                self.log.warning("file not owned by root")
                continue
            if os.stat(_file).st_mode & stat.S_IXUSR != 64:
                self.log.warning("file not executable by root")
                continue
            if os.stat(_file).st_mode & stat.S_IWOTH == 2:
                self.log.warning("file group writeable")
                continue
            if os.stat(_file).st_mode & stat.S_IWGRP == 16:
                self.log.warning("file world writeable")
                continue
            files.append(_file)
        return files

    def needs_update(self):
        update = False
        self.log.info("checking if updates are available")
        files = self.get_scripts("needs_update.d")
        for _file in files:
            self.log.info(f"running: {_file}")
            return_code = self.execute_shell([_file])
            if return_code != 0:
                self.log.info("updates are available")
                update = True
            self.do_ext_notify(
                phase="needs_update", script=_file, return_code=return_code
            )
            self.log.info(f"running: {_file} done")
        if update:
            self.task = "lock_get"
        else:
            self.log.info("no updates available")
            self.do_ext_notify(
                phase="main", script="none", return_code=0, updater_running=False
            )
            sys.exit(0)

    def update(self):
        self.log.info("running_update scripts")
        files = self.get_scripts("update.d")
        for _file in files:
            self.log.info(f"running: {_file}")
            return_code = self.execute_shell([_file])
            if return_code != 0:
                self.log.info("script failed, stopping, keeping lock")
                self.on_failure(phase="update", script=_file, return_code=return_code)
                sys.exit(1)
            self.do_ext_notify(phase="update", script=_file, return_code=return_code)
            self.log.info(f"running: {_file} done")
        self.task = "needs_reboot"

    def post_update(self):
        self.log.info("running post_update scripts")
        self.dlm_lock_acquired = True
        files = self.get_scripts("post_update.d")
        for _file in files:
            self.log.info(f"running: {_file}")
            return_code = self.execute_shell([_file])
            if return_code != 0:
                self.log.info("script failed, stopping, keeping lock")
                self.on_failure(
                    phase="post_update", script=_file, return_code=return_code
                )
                sys.exit(1)
            self.do_ext_notify(
                phase="post_update", script=_file, return_code=return_code
            )
            self.log.info(f"running: {_file} done")
        self.task = "lock_release"

    def pre_update(self):
        self.log.info("running pre_update scripts")
        files = self.get_scripts("pre_update.d")
        for _file in files:
            self.log.info(f"running: {_file}")
            return_code = self.execute_shell([_file])
            if return_code != 0:
                self.log.info("script failed, stopping, keeping lock")
                self.on_failure(
                    phase="pre_update", script=_file, return_code=return_code
                )
                sys.exit(1)
            self.do_ext_notify(
                phase="pre_update", script=_file, return_code=return_code
            )
            self.log.info(f"running: {_file} done")
        self.task = "update"

    def reboot(self):
        self.log.info("rebooting")
        self.task = "post_update"
        sys.exit(self.execute_shell([self.config.get("main", "reboot_cmd")]))

    def needs_reboot(self):
        self.log.info("running needs reboot scripts")
        reboot = True
        files = self.get_scripts("needs_reboot.d")
        for _file in files:
            self.log.info(f"running: {_file}")
            return_code = self.execute_shell([_file])
            if return_code != 0:
                self.log.info(f"running: {_file} done")
                self.do_ext_notify(
                    phase="needs_reboot", script=_file, return_code=return_code
                )
                reboot = True
                break
            else:
                reboot = False
            self.log.info(f"running: {_file} done")
        if reboot:
            self.task = "reboot"
        else:
            self.task = "post_update"

    def work(self):
        self.check_date_constraint()
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
