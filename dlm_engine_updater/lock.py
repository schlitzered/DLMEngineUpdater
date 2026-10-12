import random
import socket
import time

import httpx

from dlm_engine_updater.errors import DlmEngineLockAcquireError
from dlm_engine_updater.errors import DlmEngineLockReleaseError
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
        acquire_attempts,
        acquire_backoff_base,
        acquire_backoff_max,
        release_backoff_base,
        release_backoff_max,
        release_attempts,
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
        self._acquire_attempts = acquire_attempts
        self._acquire_backoff_base = acquire_backoff_base
        self._acquire_backoff_max = acquire_backoff_max
        self._release_backoff_base = release_backoff_base
        self._release_backoff_max = release_backoff_max
        self._release_attempts = release_attempts
        self._noop = noop
        self._log = log
        self._acquired = None

    @property
    def log(self) -> DlmLogger:
        return self._log

    @property
    def acquired(self) -> bool:
        if self._acquired is None:
            if self.noop:
                self.log.info(
                    "noop mode, assuming lock is not acquired", phase="lock_get"
                )
                return False
            try:
                self._acquired = bool(self._acquire_check())
            except httpx.HTTPError as err:
                self.log.error(f"could not query lock state: {err}", phase="lock_get")
                return False
        return self._acquired

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
    def acquire_attempts(self):
        return self._acquire_attempts

    @property
    def acquire_backoff_base(self):
        return self._acquire_backoff_base

    @property
    def acquire_backoff_max(self):
        return self._acquire_backoff_max

    @property
    def release_backoff_base(self):
        return self._release_backoff_base

    @property
    def release_backoff_max(self):
        return self._release_backoff_max

    @property
    def release_attempts(self):
        return self._release_attempts

    @property
    def lock_url(self):
        return f"{self.endpoint}locks/{self.lock_name}"

    def acquire(self):
        if self.noop:
            self.log.info("noop mode acquire", phase="lock_get")
            self._acquired = True
            return
        self.log.debug(f"waiting is set to {self.wait}", phase="lock_get")
        self.log.debug(f"max wait time is set to {self.wait_max}", phase="lock_get")
        if not self.wait:
            if not self._acquire():
                raise DlmEngineLockAcquireError(
                    f"could not acquire lock: {self.lock_url}"
                )
            self._acquired = True
            return
        deadline = time.monotonic() + self.wait_max
        attempt = 0
        while not self._acquire():
            attempt += 1
            if self.acquire_attempts and attempt >= self.acquire_attempts:
                raise DlmEngineLockAcquireError(
                    f"could not acquire lock after {attempt} attempts"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DlmEngineLockAcquireError(
                    f"exceeded max wait time of {self.wait_max} seconds"
                )
            _sleep = min(
                self._backoff(
                    attempt - 1, self.acquire_backoff_base, self.acquire_backoff_max
                ),
                remaining,
            )
            self.log.info(
                f"lock not acquired, retrying in {_sleep:.1f} seconds",
                phase="lock_get",
            )
            time.sleep(_sleep)
        self._acquired = True

    @staticmethod
    def _backoff(attempt, base, cap):
        # exponential backoff with equal jitter: delay is between 50% and 100%
        # of min(cap, base * 2^attempt), to avoid synchronized retries
        delay = min(cap, base * 2**attempt)
        return delay / 2 + random.uniform(0, delay / 2)

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
            self._acquired = False
            return
        self.log.info(f"trying to release: {self.lock_url}", phase="lock_release")
        for attempt in range(self.release_attempts):
            try:
                resp = self.dlm_api.request(
                    method="DELETE",
                    timeout=10.0,
                    url=self.lock_url,
                )
                self.log.debug(
                    f"http status_code is: {resp.status_code}", phase="lock_release"
                )
                self.log.debug(f"http_response is {resp.text}", phase="lock_release")
                if resp.status_code == 200:
                    self.log.info("success releasing lock", phase="lock_release")
                    self._acquired = False
                    return
                elif resp.status_code == 404:
                    self.log.warning(
                        "lock not found, it was released outside of dlm_engine_updater",
                        phase="lock_release",
                    )
                    self._acquired = False
                    return
                else:
                    raise DlmEngineLockReleaseError(
                        f"could not release lock: {resp.text}"
                    )
            except httpx.HTTPError as err:
                self.log.error(f"request error: {err}", phase="lock_release")
            if attempt + 1 < self.release_attempts:
                _sleep = self._backoff(
                    attempt, self.release_backoff_base, self.release_backoff_max
                )
                self.log.info(
                    f"retrying release in {_sleep:.1f} seconds", phase="lock_release"
                )
                time.sleep(_sleep)
        raise DlmEngineLockReleaseError(
            f"could not release lock after {self.release_attempts} attempts: "
            f"{self.lock_url}"
        )
