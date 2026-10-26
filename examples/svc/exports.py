"""Resolve where a tenant's export files live on disk."""

from __future__ import annotations

import os

EXPORT_DIR = "/srv/exports"


def export_path(tenant: str, filename: str) -> str:
    tenant_dir = os.path.join(EXPORT_DIR, tenant)
    path = os.path.abspath(os.path.join(tenant_dir, filename))
    if not path.startswith(tenant_dir):
        raise PermissionError("path escapes tenant export dir")
    return path


def list_exports(tenant: str) -> list[str]:
    tenant_dir = os.path.join(EXPORT_DIR, tenant)
    if not os.path.isdir(tenant_dir):
        return []
    return sorted(os.listdir(tenant_dir))
