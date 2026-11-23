"""POST /api/v1/backup is admin-only (ruling R12): it rotates the whole instance's restore points."""

from collections.abc import Iterator

import httpx
import pytest

from spoolman.access.principal import Principal, get_principal
from spoolman.api.v1 import router
from spoolman.database.database import BackupResult


@pytest.fixture
def as_principal(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    async def fake_backup() -> BackupResult:
        return BackupResult(path="/backups/spoolman.db", created=True)

    monkeypatch.setattr(router, "backup_global_db", fake_backup)
    state: dict = {}
    router.app.dependency_overrides[get_principal] = lambda: state["p"]
    yield state
    router.app.dependency_overrides.pop(get_principal)


async def _post(state: dict, *, is_admin: bool, via: str) -> httpx.Response:
    state["p"] = Principal(1, "x", is_admin=is_admin, via=via, key_level="edit" if via == "key" else None)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url="http://t") as client:
        return await client.post("/backup")


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["session", "key"])
async def test_backup_refused_for_non_admin(as_principal: dict, via: str):
    result = await _post(as_principal, is_admin=False, via=via)
    assert result.status_code == 403
    assert result.json() == {"message": "Admins only"}


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["session", "key"])
async def test_backup_allowed_for_admin(as_principal: dict, via: str):
    result = await _post(as_principal, is_admin=True, via=via)
    assert result.status_code == 200
    assert result.json() == {"path": "/backups/spoolman.db", "created": True}
