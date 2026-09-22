from pathlib import Path

import pytest

from app.services.freeze import FreezeError, FreezeService


def test_formal_write_is_fail_closed_when_disabled(tmp_path: Path):
    history = tmp_path / "history.json"
    history.write_text('{"records": []}', encoding="utf-8")
    service = FreezeService(tmp_path, history, tmp_path / "backups")
    with pytest.raises(FreezeError, match="未启用"):
        service.commit(
            {}, source_path=tmp_path / "candidate.json", idempotency_key="abcdefgh",
            expected_history_sha256="0" * 64, enabled=False,
        )

