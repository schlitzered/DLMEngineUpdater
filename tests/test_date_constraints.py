import datetime
import types
from unittest import mock

import pytest

from dlm_engine_updater import updater as updater_module
from dlm_engine_updater.updater import DlmEngineUpdater


@pytest.fixture
def updater():
    # bypass __init__, it needs a config file, log files and a pid file
    _updater = DlmEngineUpdater.__new__(DlmEngineUpdater)
    _updater._log = mock.Mock()
    _updater._date_constraints = None
    return _updater


@pytest.fixture
def freeze_now(monkeypatch):
    def _freeze(year, month, day, hour=12):
        class FrozenDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(year, month, day, hour)

        monkeypatch.setattr(
            updater_module,
            "datetime",
            types.SimpleNamespace(
                datetime=FrozenDatetime, timedelta=datetime.timedelta
            ),
        )

    return _freeze


class TestParseDateConstraints:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("1:Mon", [{"nth": 1, "day": "Mon"}]),
            ("3:Fri", [{"nth": 3, "day": "Fri"}]),
            ("5:Sun", [{"nth": 5, "day": "Sun"}]),
            (
                "1:Mon,3:Fri",
                [{"nth": 1, "day": "Mon"}, {"nth": 3, "day": "Fri"}],
            ),
        ],
    )
    def test_valid(self, updater, value, expected):
        updater.date_constraints = value
        assert updater.date_constraints == expected

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty(self, updater, value):
        updater.date_constraints = value
        assert updater.date_constraints is None

    @pytest.mark.parametrize(
        "value",
        [
            "3Fri",  # missing separator
            "x:Fri",  # number not an int
            ":Fri",  # number missing
            "0:Fri",  # number too small
            "6:Fri",  # number too large
            "-1:Fri",  # negative number
            "3:",  # day missing
            "3:Friday",  # full day name instead of abbreviation
            "3:fri",  # wrong case
            "3:Fri:x",  # trailing garbage
            "3:Fri,6:Mon",  # one valid, one invalid
            "3:Fri,",  # trailing comma
        ],
    )
    def test_invalid(self, updater, value):
        with pytest.raises(SystemExit) as exc:
            updater.date_constraints = value
        assert exc.value.code == 1


class TestCheckDateConstraint:
    # October 2026: Fridays are the 2nd, 9th, 16th, 23rd and 30th
    @pytest.mark.parametrize(
        "today, nth, day",
        [
            ((2026, 10, 2), 1, "Fri"),
            ((2026, 10, 16), 3, "Fri"),
            ((2026, 10, 30), 5, "Fri"),
            ((2026, 10, 1), 1, "Thu"),
            ((2026, 11, 30), 5, "Mon"),
        ],
    )
    def test_matches(self, updater, freeze_now, today, nth, day):
        freeze_now(*today)
        assert updater._check_date_constraint(nth=nth, day=day) is True

    @pytest.mark.parametrize(
        "today, nth, day",
        [
            ((2026, 10, 16), 3, "Thu"),  # wrong weekday
            ((2026, 10, 16), 2, "Fri"),  # 3rd Friday, not 2nd
            ((2026, 10, 16), 4, "Fri"),  # 3rd Friday, not 4th
            ((2026, 10, 23), 5, "Fri"),  # 4th Friday, not 5th
            ((2026, 11, 6), 5, "Fri"),  # 1st Friday, not 5th
        ],
    )
    def test_no_match(self, updater, freeze_now, today, nth, day):
        freeze_now(*today)
        assert updater._check_date_constraint(nth=nth, day=day) is False


class TestCheckDateConstraints:
    def test_no_constraints(self, updater):
        assert updater.check_date_constraints() is None

    def test_one_of_multiple_matches(self, updater, freeze_now):
        freeze_now(2026, 10, 16)
        updater.date_constraints = "1:Mon,3:Fri"
        assert updater.check_date_constraints() is True

    def test_none_matches_exits_cleanly(self, updater, freeze_now):
        freeze_now(2026, 10, 16)
        updater.date_constraints = "1:Mon,2:Fri"
        with pytest.raises(SystemExit) as exc:
            updater.check_date_constraints()
        assert exc.value.code == 0
