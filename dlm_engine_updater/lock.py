import random
import socket
import sys
import time

import httpx

from dlm_engine_updater.logger import DlmLogger


class DlmEngineLock:
    def __init__(
        self,
        log,
        lock_name,
        ca,
        secret,
        secret_id,
        endpoint,
        wait,
        wait_max,
        noop,
    ):
        self._ca = ca
        self._endpoint = endpoint
        self._lock_name = lock_name
        self._secret = secret
        self._secret_id = secret_id
        self._dlm_api = None
        self._wait = wait
        self._wait_max = wait_max
        self._noop = noop
        self._log = log

    @property
    def log(self) -> DlmLogger:
        return self._log

    @property
    def ca(self):
        return self._ca

    @property
    def endpoint(self):
        return self._endpoint

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
                    "x-secret-id": self.secret_id,
                    "x-secret": self.secret,
                },
            )
        return self._dlm_api

    @property
    def lock_name(self):
        return self._lock_name

    @property
    def noop(self):
        return self._noop

    @property
    def payload_acquire(self):
        return {"acquired_by": socket.getfqdn()}

    @property
    def wait(self):
        return self._wait

    @property
    def wait_max(self):
        return self._wait_max

    @property
    def lock_url(self):
        return f"{self.endpoint}locks/{self.lock_name}"

    def acquire(self):
        if self.noop:
            self.log.info("noop mode acquire", phase="lock_get")
            return
        self.log.debug(f"waiting is set to {self.wait}", phase="lock_get")
        self.log.debug(f"max wait time is set to {self.wait_max}", phase="lock_get")
        if self.wait:
            _waited = 0
            while True:
                if self._acquire():
                    return
                else:
                    if _waited > self.wait_max:
                        self.log.error(
                            "exceeded max wait time, quiting", phase="lock_get"
                        )
                        sys.exit(1)
                    _sleep = random.randint(10, 60)
                    _waited += _sleep + 2
                    self.log.error(f"sleeping {_sleep} seconds", phase="lock_get")
                    time.sleep(_sleep)
        else:
            if not self._acquire():
                self.log.error("quiting", phase="lock_get")
                sys.exit(1)

    def _acquire(self):
        self.log.info(f"trying to acquire: {self.lock_url}", phase="lock_get")
        if self._acquire_check():
            return True
        try:
            resp = self.dlm_api.post(
                json=self.payload_acquire,
                timeout=10.0,
                url=self.lock_url,
            )
            self.log.debug(f"http status_code is: {resp.status_code}", phase="lock_get")
            self.log.debug(f"http_response is {resp.json()}", phase="lock_get")
            if resp.status_code == 201:
                self.log.info("success acquiring lock", phase="lock_get")
                return True
            else:
                self.log.error(
                    f"could not acquire lock: {resp.json()}", phase="lock_get"
                )
                return False
        except httpx.HTTPError as err:
            self.log.error(f"request error, retrying: {err}", phase="lock_get")
            return False

    def _acquire_check(self):
        self.log.info("checking if lock has been acquired", phase="lock_get")
        resp = self.dlm_api.get(
            timeout=10.0,
            url=self.lock_url,
        )
        if not resp.status_code == 200:
            self.log.info("lock currently not present in the system", phase="lock_get")
            return None
        if not resp.json()["acquired_by"] == socket.getfqdn():
            self.log.info(
                f"lock is currently acquired by {resp.json()['acquired_by']}",
                phase="lock_get",
            )
            return None

        self.log.info(
            "lock has been already acquired by this instance", phase="lock_get"
        )
        return True

    def release(self):
        if self.noop:
            self.log.info("noop mode release", phase="lock_release")
            return
        self.log.info(f"trying to release: {self.lock_url}", phase="lock_release")
        retries = 10
        while retries > 0:
            try:
                resp = self.dlm_api.request(
                    method="DELETE",
                    timeout=10.0,
                    url=self.lock_url,
                )
                self.log.debug(
                    f"http status_code is: {resp.status_code}", phase="lock_release"
                )
                self.log.debug(f"http_response is {resp.json()}", phase="lock_release")
                if resp.status_code == 200:
                    self.log.info("success releasing lock", phase="lock_release")
                    return
                else:
                    self.log.error(
                        f"could not release lock: {resp.json()}", phase="lock_release"
                    )
                    sys.exit(1)
            except (httpx.HTTPError, httpx.ConnectError) as err:
                self.log.error(f"request error, retrying: {err}", phase="lock_release")
                retries -= 1
                time.sleep(5)
        self.log.fatal("could not release lock", phase="lock_release")
        sys.exit(1)
