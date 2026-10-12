import pydantic
import pytest

from dlm_engine_updater.config import DlmUpdaterConfigMain


def test_lock_retry_defaults():
    main = DlmUpdaterConfigMain()
    assert main.acquireattempts == 0
    assert main.acquirebackoffbase == 10
    assert main.acquirebackoffmax == 120
    assert main.releasebackoffbase == 1
    assert main.releasebackoffmax == 30
    assert main.releaseattempts == 10
    assert main.resetstateonreleasefailure is False


@pytest.mark.parametrize(
    "field, value",
    [
        ("acquireattempts", -1),
        ("acquirebackoffbase", 0),
        ("acquirebackoffmax", 0),
        ("releasebackoffbase", 0),
        ("releasebackoffmax", -1),
        ("releaseattempts", 0),
    ],
)
def test_lock_retry_invalid(field, value):
    with pytest.raises(pydantic.ValidationError):
        DlmUpdaterConfigMain(**{field: value})
