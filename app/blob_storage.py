"""
Thin wrapper around Azure Blob Storage.

Auth order:
  1. If AZURE_STORAGE_CONNECTION_STRING is set -> use it (dev / fallback only).
  2. Otherwise use DefaultAzureCredential -> Managed Identity in Azure,
     az login / env credentials locally. This is the recommended path and
     requires the app's identity to have the "Storage Blob Data Contributor"
     role on the storage account.
"""
from __future__ import annotations

import logging

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .config import get_settings

logger = logging.getLogger(__name__)


class BlobNotFoundError(FileNotFoundError):
    """Raised when the requested Excel blob does not exist."""


def _service_client() -> BlobServiceClient:
    s = get_settings()
    if s.azure_storage_connection_string:
        logger.debug("Using storage connection string.")
        return BlobServiceClient.from_connection_string(s.azure_storage_connection_string)
    logger.debug("Using DefaultAzureCredential for storage.")
    return BlobServiceClient(
        account_url=s.azure_storage_account_url,
        credential=DefaultAzureCredential(),
    )


def download_excel(blob_name: str) -> bytes:
    """Download an Excel blob from the input container and return its bytes."""
    s = get_settings()
    client = _service_client().get_blob_client(
        container=s.input_container, blob=blob_name
    )
    try:
        logger.info("Downloading '%s' from container '%s'.", blob_name, s.input_container)
        return client.download_blob().readall()
    except ResourceNotFoundError as exc:
        raise BlobNotFoundError(
            f"Blob '{blob_name}' not found in container '{s.input_container}'."
        ) from exc


def upload_csv(blob_name: str, data: bytes, *, overwrite: bool = True) -> str:
    """
    Upload one CSV to the output container (with optional prefix) and return
    the full blob URL.
    """
    s = get_settings()
    full_name = f"{s.output_prefix}{blob_name}" if s.output_prefix else blob_name
    client = _service_client().get_blob_client(
        container=s.output_container, blob=full_name
    )
    client.upload_blob(
        data,
        overwrite=overwrite,
        content_settings=ContentSettings(content_type="text/csv"),
    )
    logger.info("Uploaded '%s' (%d bytes) to '%s'.", full_name, len(data), s.output_container)
    return client.url
