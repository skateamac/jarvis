import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    core_url: str = "http://core:8000"
    policy_url: str = "http://policy:8000"
    connectors_url: str = "http://connectors-google:8000"
    scheduler_url: str = "http://scheduler:8000"
    alexa_skip_verify: bool = False
    household_pin: str = "1234"
    policy_version: str = "v0.1"
    database_url: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    google_account_key: str = "household"
    google_calendar_family_id: str = ""
    google_calendar_spouse_id: str = ""
    google_calendar_work_id: str = ""
    google_tasks_list_household: str = ""
    google_tasks_list_shopping: str = ""
    google_tasks_list_personal: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            core_url=os.getenv("JARVIS_CORE_URL", cls.core_url),
            policy_url=os.getenv("JARVIS_POLICY_URL", cls.policy_url),
            connectors_url=os.getenv("JARVIS_CONNECTORS_URL", cls.connectors_url),
            scheduler_url=os.getenv("JARVIS_SCHEDULER_URL", cls.scheduler_url),
            alexa_skip_verify=_env_bool("JARVIS_ALEXA_SKIP_VERIFY", default=False),
            household_pin=os.getenv("JARVIS_HOUSEHOLD_PIN", cls.household_pin),
            policy_version=os.getenv("JARVIS_POLICY_VERSION", cls.policy_version),
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", cls.google_client_id),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", cls.google_client_secret),
            google_redirect_uri=os.getenv("GOOGLE_REDIRECT_URI", cls.google_redirect_uri),
            google_account_key=os.getenv("GOOGLE_ACCOUNT_KEY", cls.google_account_key),
            google_calendar_family_id=os.getenv("GOOGLE_CALENDAR_FAMILY_ID", cls.google_calendar_family_id),
            google_calendar_spouse_id=os.getenv("GOOGLE_CALENDAR_SPOUSE_ID", cls.google_calendar_spouse_id),
            google_calendar_work_id=os.getenv("GOOGLE_CALENDAR_WORK_ID", cls.google_calendar_work_id),
            google_tasks_list_household=os.getenv("GOOGLE_TASKS_LIST_HOUSEHOLD", cls.google_tasks_list_household),
            google_tasks_list_shopping=os.getenv("GOOGLE_TASKS_LIST_SHOPPING", cls.google_tasks_list_shopping),
            google_tasks_list_personal=os.getenv("GOOGLE_TASKS_LIST_PERSONAL", cls.google_tasks_list_personal),
        )


settings = Settings.from_env()
