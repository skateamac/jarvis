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
        )


settings = Settings.from_env()
