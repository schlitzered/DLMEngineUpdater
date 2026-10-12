class DlmEngineLockError(Exception):
    pass


class DlmEngineLockAcquireError(DlmEngineLockError):
    pass


class DlmEngineLockReleaseError(DlmEngineLockError):
    pass
