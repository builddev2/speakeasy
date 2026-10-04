"""Launch-at-login via SMAppService (installed .app, macOS 13+ only)."""

import sys

import objc


def _service():
    """SMAppService for the running bundle, or None when not applicable.

    Launch-at-login only makes sense for the packaged .app (a bare python
    process has no bundle to register), and the API is macOS 13+.
    """
    if not getattr(sys, "frozen", False):
        return None
    try:
        sm = {}
        objc.loadBundle(
            "ServiceManagement",
            sm,
            bundle_path="/System/Library/Frameworks/ServiceManagement.framework",
        )
        return objc.lookUpClass("SMAppService").mainAppService()
    except Exception:
        return None


def status() -> bool | None:
    service = _service()
    if service is None:
        return None
    return service.status() == 1  # SMAppServiceStatusEnabled


def set_enabled(enabled: bool) -> None:
    service = _service()
    if service is None:
        raise RuntimeError("Start at login is available in the installed app.")
    if enabled == (service.status() == 1):
        return
    if enabled:
        ok, err = service.registerAndReturnError_(None)
    else:
        ok, err = service.unregisterAndReturnError_(None)
    if not ok:
        raise RuntimeError(str(err))
