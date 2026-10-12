import types
from unittest import mock

import pytest

from dlm_engine_updater.errors import DlmEngineLockReleaseError
from dlm_engine_updater.updater import DlmEngineUpdater


@pytest.fixture
def make_updater(tmp_path):
    def _make(resetstateonreleasefailure=False):
        # bypass __init__, it needs a config file, log files and a pid file
        updater = DlmEngineUpdater.__new__(DlmEngineUpdater)
        updater._log = mock.Mock()
        updater._config = types.SimpleNamespace(
            main=types.SimpleNamespace(
                basedir=str(tmp_path),
                resetstateonreleasefailure=resetstateonreleasefailure,
            )
        )
        updater._dlm_lock = mock.Mock()
        updater.task = "lock_release"
        return updater

    return _make


class TestDlmLockRelease:
    def test_success_removes_state(self, make_updater, tmp_path):
        updater = make_updater()
        with mock.patch.object(DlmEngineUpdater, "do_ext_notify"):
            with pytest.raises(SystemExit) as exc:
                updater.dlm_lock_release()
        assert exc.value.code == 0
        assert not (tmp_path / "state").exists()

    def test_failure_keeps_state_by_default(self, make_updater, tmp_path):
        updater = make_updater()
        updater.dlm_lock.release.side_effect = DlmEngineLockReleaseError("boom")
        with pytest.raises(SystemExit) as exc:
            updater.dlm_lock_release()
        assert exc.value.code == 1
        assert updater.task == "lock_release"

    def test_failure_resets_state_if_enabled(self, make_updater, tmp_path):
        updater = make_updater(resetstateonreleasefailure=True)
        updater.dlm_lock.release.side_effect = DlmEngineLockReleaseError("boom")
        with pytest.raises(SystemExit) as exc:
            updater.dlm_lock_release()
        assert exc.value.code == 1
        assert not (tmp_path / "state").exists()
        assert updater.task == "needs_update"
        updater.log.warning.assert_called_once()
