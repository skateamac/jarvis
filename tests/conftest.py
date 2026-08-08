import os
import sys

import httpx2
import pytest

from shared.jarvis_common.config import Settings
from shared.jarvis_common.db.oauth_store import oauth_token_store
from shared.jarvis_common.stores import approval_store, audit_store

sys.modules.setdefault("httpx", httpx2)

os.environ.setdefault("JARVIS_USE_LOCAL_CLIENTS", "1")
os.environ.setdefault("JARVIS_ALEXA_SKIP_VERIFY", "1")
os.environ.setdefault("JARVIS_HOUSEHOLD_PIN", "1234")


@pytest.fixture(autouse=True)
def reset_stores() -> None:
    approval_store.clear()
    audit_store.clear()
    oauth_token_store.clear()


@pytest.fixture(autouse=True)
def reset_settings() -> None:
    from shared.jarvis_common import alexa as alexa_module
    from shared.jarvis_common import config as config_module

    config_module.settings = Settings.from_env()
    alexa_module.settings = config_module.settings
