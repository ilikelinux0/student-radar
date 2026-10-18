"""Keep sweep snapshots out of the repo during tests."""

from pathlib import Path

import pytest

import radar.sweep
from radar.models import Settings

_DEFAULT_EXPORT_DIR = Settings.model_fields["export_dir"].default


@pytest.fixture(autouse=True)
def _keep_snapshots_out_of_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = radar.sweep._export_destination

    def destination(settings: Settings, override: Path | None) -> Path:
        if override is None and settings.export_dir == _DEFAULT_EXPORT_DIR:
            return tmp_path / "snapshot"
        return real(settings, override)

    monkeypatch.setattr(radar.sweep, "_export_destination", destination)
