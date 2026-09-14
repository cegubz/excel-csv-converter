"""
Central configuration, read from environment variables (see .env.example).

At runtime on Foundry these come from the hosted-agent's environment settings
(declared in azure.yaml). Locally they come from a .env file.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Blob storage ---
    azure_storage_account_url: str = Field(default="", alias="AZURE_STORAGE_ACCOUNT_URL")
    azure_storage_connection_string: str | None = Field(
        default=None, alias="AZURE_STORAGE_CONNECTION_STRING"
    )
    input_container: str = Field(default="incoming", alias="INPUT_CONTAINER")
    output_container: str = Field(default="converted", alias="OUTPUT_CONTAINER")
    output_prefix: str = Field(default="", alias="OUTPUT_PREFIX")

    # --- Output naming / logging ---
    csv_name_template: str = Field(default="{stem}__{sheet}.csv", alias="CSV_NAME_TEMPLATE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    def validate_runtime(self) -> None:
        """Fail fast with a clear message if required settings are missing."""
        if not self.azure_storage_account_url and not self.azure_storage_connection_string:
            raise RuntimeError(
                "Invalid configuration: set AZURE_STORAGE_ACCOUNT_URL (recommended, "
                "with Managed Identity) or AZURE_STORAGE_CONNECTION_STRING."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
