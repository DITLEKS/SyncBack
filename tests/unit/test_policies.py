"""Ограничения домена, задаваемые конфигурацией."""

from app.domain.policies import UploadLimits


def test_upload_limits_from_megabytes() -> None:
    limits = UploadLimits.from_megabytes(2)
    assert limits.max_size_bytes == 2 * 1024 * 1024
    assert limits.max_size_mb == 2
    assert not limits.exceeded_by(limits.max_size_bytes)
    assert limits.exceeded_by(limits.max_size_bytes + 1)
