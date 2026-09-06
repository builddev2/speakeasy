from speakeasy import settings


def test_source_build_commit_is_safe_identifier():
    value = settings.build_commit()

    assert value in {"development", "unknown"} or settings._BUILD_COMMIT_RE.fullmatch(value)
