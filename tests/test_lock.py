from unittest import mock

import httpx
import pytest

from dlm_engine_updater.errors import DlmEngineLockAcquireError
from dlm_engine_updater.errors import DlmEngineLockReleaseError
from dlm_engine_updater.lock import DlmEngineLock


def make_lock(**kwargs):
    params = dict(
        log=mock.Mock(),
        lock_name="lock",
        ca=None,
        secret="secret",
        secret_id="secret_id",
        endpoint="http://dlm.example.com/",
        wait=True,
        wait_max=3600,
        acquire_attempts=0,
        acquire_backoff_base=10,
        acquire_backoff_max=120,
        release_backoff_base=1,
        release_backoff_max=30,
        release_attempts=10,
        noop=False,
    )
    params.update(kwargs)
    return DlmEngineLock(**params)


@pytest.fixture
def clock(monkeypatch):
    # fake clock: time.sleep advances time.monotonic, sleeps are recorded
    state = {"now": 0.0, "sleeps": []}

    def _sleep(seconds):
        state["sleeps"].append(seconds)
        state["now"] += seconds

    monkeypatch.setattr("dlm_engine_updater.lock.time.sleep", _sleep)
    monkeypatch.setattr("dlm_engine_updater.lock.time.monotonic", lambda: state["now"])
    return state


class TestBackoff:
    @pytest.mark.parametrize(
        "attempt, expected",
        [(0, 10), (1, 20), (2, 40), (3, 80), (4, 120), (10, 120)],
    )
    def test_bounds(self, attempt, expected):
        for _ in range(100):
            delay = DlmEngineLock._backoff(attempt, base=10, cap=120)
            assert expected / 2 <= delay <= expected


class TestAcquire:
    def test_success_first_try(self, clock):
        lock = make_lock()
        with mock.patch.object(DlmEngineLock, "_acquire", return_value=True):
            lock.acquire()
        assert lock.acquired is True
        assert clock["sleeps"] == []

    def test_success_after_retries(self, clock):
        lock = make_lock()
        with mock.patch.object(
            DlmEngineLock, "_acquire", side_effect=[False, False, True]
        ):
            lock.acquire()
        assert lock.acquired is True
        assert len(clock["sleeps"]) == 2

    def test_no_wait_fails_immediately(self, clock):
        lock = make_lock(wait=False, acquire_attempts=5)
        with mock.patch.object(DlmEngineLock, "_acquire", return_value=False) as m:
            with pytest.raises(DlmEngineLockAcquireError):
                lock.acquire()
        assert m.call_count == 1
        assert clock["sleeps"] == []

    def test_wait_max_exceeded(self, clock):
        lock = make_lock(wait_max=100)
        with mock.patch.object(DlmEngineLock, "_acquire", return_value=False):
            with pytest.raises(DlmEngineLockAcquireError, match="max wait time"):
                lock.acquire()
        # the last sleep is shortened so the total never exceeds wait_max
        assert sum(clock["sleeps"]) == pytest.approx(100)

    def test_acquire_attempts_exceeded(self, clock):
        lock = make_lock(acquire_attempts=3)
        with mock.patch.object(DlmEngineLock, "_acquire", return_value=False) as m:
            with pytest.raises(DlmEngineLockAcquireError, match="3 attempts"):
                lock.acquire()
        assert m.call_count == 3
        assert len(clock["sleeps"]) == 2

    def test_acquire_attempts_unlimited(self, clock):
        lock = make_lock(acquire_attempts=0, wait_max=10000)
        with mock.patch.object(
            DlmEngineLock, "_acquire", side_effect=[False] * 50 + [True]
        ):
            lock.acquire()
        assert lock.acquired is True

    def test_wait_max_wins_over_acquire_attempts(self, clock):
        lock = make_lock(acquire_attempts=100, wait_max=30)
        with mock.patch.object(DlmEngineLock, "_acquire", return_value=False):
            with pytest.raises(DlmEngineLockAcquireError, match="max wait time"):
                lock.acquire()

    def test_noop(self, clock):
        lock = make_lock(noop=True)
        with mock.patch.object(DlmEngineLock, "_acquire") as m:
            lock.acquire()
        m.assert_not_called()
        assert lock.acquired is True


def json_response(status_code, body):
    return httpx.Response(status_code, json=body)


def bad_gateway():
    # load balancers answer with an html body (or none at all), not json
    return httpx.Response(502, text="<html><body>502 Bad Gateway</body></html>")


class TestAcquireRequest:
    def test_created(self):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.get.return_value = json_response(404, {"detail": "not found"})
        lock._dlm_api.post.return_value = json_response(201, {"id": "lock"})
        assert lock._acquire() is True

    def test_held_by_other_host(self):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.get.return_value = json_response(
            200, {"acquired_by": "other.example.com"}
        )
        lock._dlm_api.post.return_value = json_response(
            400, {"detail": "Duplicate Resource"}
        )
        assert lock._acquire() is False

    def test_post_non_json_response(self):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.get.return_value = json_response(
            200, {"acquired_by": "other.example.com"}
        )
        lock._dlm_api.post.return_value = bad_gateway()
        assert lock._acquire() is False

    def test_check_non_json_response(self):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.get.return_value = httpx.Response(200, text="not json")
        assert lock._acquire() is False
        lock._dlm_api.post.assert_not_called()

    def test_check_connection_error(self):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.get.side_effect = httpx.ConnectError("connection refused")
        assert lock._acquire() is False
        lock._dlm_api.post.assert_not_called()

    def test_retries_after_bad_gateway(self, clock):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.get.return_value = json_response(404, {"detail": "not found"})
        lock._dlm_api.post.side_effect = [
            bad_gateway(),
            json_response(201, {"id": "lock"}),
        ]
        lock.acquire()
        assert lock.acquired is True
        assert len(clock["sleeps"]) == 1

    def test_acquired_property_non_json_response(self):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.get.return_value = httpx.Response(200, text="not json")
        assert lock.acquired is False

    def test_check_json_without_acquired_by(self):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.get.return_value = json_response(200, {"detail": "unexpected"})
        lock._dlm_api.post.return_value = json_response(
            400, {"detail": "Duplicate Resource"}
        )
        assert lock._acquire() is False


class TestRelease:
    def test_success(self, clock):
        lock = make_lock()
        lock._acquired = True
        lock._dlm_api = mock.Mock()
        lock._dlm_api.request.return_value = mock.Mock(status_code=200)
        lock.release()
        assert lock.acquired is False
        assert clock["sleeps"] == []

    def test_retries_on_request_error(self, clock):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.request.side_effect = [
            httpx.ConnectError("down"),
            httpx.ConnectError("down"),
            mock.Mock(status_code=200),
        ]
        lock.release()
        assert lock.acquired is False
        assert len(clock["sleeps"]) == 2

    def test_release_attempts_exceeded(self, clock):
        lock = make_lock(release_attempts=4)
        lock._dlm_api = mock.Mock()
        lock._dlm_api.request.side_effect = httpx.ConnectError("down")
        with pytest.raises(DlmEngineLockReleaseError, match="4 attempts"):
            lock.release()
        assert lock._dlm_api.request.call_count == 4
        # no sleep after the last attempt
        assert len(clock["sleeps"]) == 3

    def test_not_found_counts_as_success(self, clock):
        lock = make_lock()
        lock._acquired = True
        lock._dlm_api = mock.Mock()
        lock._dlm_api.request.return_value = mock.Mock(status_code=404)
        lock.release()
        assert lock.acquired is False
        assert lock._dlm_api.request.call_count == 1
        assert clock["sleeps"] == []
        lock.log.warning.assert_called_once()
        assert "outside of dlm_engine_updater" in lock.log.warning.call_args[0][0]

    def test_error_response_fails_immediately(self, clock):
        lock = make_lock()
        lock._dlm_api = mock.Mock()
        lock._dlm_api.request.return_value = mock.Mock(status_code=403)
        with pytest.raises(DlmEngineLockReleaseError):
            lock.release()
        assert lock._dlm_api.request.call_count == 1

    def test_retries_on_server_error(self, clock):
        lock = make_lock()
        lock._acquired = True
        lock._dlm_api = mock.Mock()
        lock._dlm_api.request.side_effect = [
            bad_gateway(),
            httpx.Response(503, text="Service Unavailable"),
            json_response(200, {}),
        ]
        lock.release()
        assert lock.acquired is False
        assert lock._dlm_api.request.call_count == 3
        assert len(clock["sleeps"]) == 2

    def test_server_error_attempts_exceeded(self, clock):
        lock = make_lock(release_attempts=4)
        lock._dlm_api = mock.Mock()
        lock._dlm_api.request.return_value = bad_gateway()
        with pytest.raises(DlmEngineLockReleaseError, match="4 attempts"):
            lock.release()
        assert lock._dlm_api.request.call_count == 4
        assert len(clock["sleeps"]) == 3

    def test_noop(self, clock):
        lock = make_lock(noop=True)
        lock._dlm_api = mock.Mock()
        lock.release()
        lock._dlm_api.request.assert_not_called()
        assert lock.acquired is False
