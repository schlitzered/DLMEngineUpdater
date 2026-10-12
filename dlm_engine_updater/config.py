import sys
import typing

from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class DLMEngineUpdaterMainApi(BaseModel):
    noop: typing.Optional[bool] = False
    ca: typing.Optional[str] = None
    endpoint: typing.Optional[str] = None
    lockname: typing.Optional[str] = None
    secret: typing.Optional[str] = None
    secretid: typing.Optional[str] = None


class DlmUpdaterConfigMainLog(BaseModel):
    level: str = "DEBUG"
    retention: typing.Optional[int] = 7
    file: typing.Optional[str] = "/var/log/dlm_engine_updater/dlm_engine_updater.log"


class DlmUpdaterConfigMainPlugin(BaseModel):
    enabled: typing.Optional[bool] = True
    config: typing.Optional[dict[str, str]] = None


class DlmUpdaterConfigMain(BaseModel):
    api: typing.Optional[DLMEngineUpdaterMainApi] = DLMEngineUpdaterMainApi()
    log: typing.Optional[DlmUpdaterConfigMainLog] = DlmUpdaterConfigMainLog()
    basedir: typing.Optional[str] = "/etc/dlm_engine_updater"
    wait: typing.Optional[bool] = False
    waitmax: typing.Optional[int] = 3600
    acquirebackoffbase: float = Field(default=10, gt=0)
    acquirebackoffmax: float = Field(default=120, gt=0)
    releasebackoffbase: float = Field(default=1, gt=0)
    releasebackoffmax: float = Field(default=30, gt=0)
    releaseattempts: int = Field(default=10, ge=1)
    userscriptusers: typing.Optional[typing.List[str]] = None


class DlmUpdaterConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="/etc/dlm_engine_updater/.env", env_nested_delimiter="_"
    )
    main: DlmUpdaterConfigMain = DlmUpdaterConfigMain()
    plugin: typing.Optional[dict[str, DlmUpdaterConfigMainPlugin]] = None

    @model_validator(mode="after")
    @classmethod
    def check_config(cls, values):
        errors = False
        if not values.main.api.lockname:
            print("main_api_lockName is required")
            errors = True
        if not values.main.api.endpoint:
            print("main_api_endpoint is required")
            errors = True
        if not values.main.api.secretid:
            print("main_api_secretId is required")
            errors = True
        if not values.main.api.secret:
            print("main_api_secret is required")
            errors = True
        if errors:
            sys.exit(1)
        return values
