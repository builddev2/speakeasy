"""WKWebView host for the glass windows.

WebKit/AppKit are imported lazily inside WebWindow so this module stays
importable in tests (same pattern as diarizer.py's lazy sherpa_onnx).
All methods are main-thread only — callers hop with
performSelectorOnMainThread first, exactly like the existing windows.
"""

from __future__ import annotations

import json

from speakeasy import settings
from speakeasy.ui.webbridge import BridgeDispatcher, EvalQueue

_handler_classes = None


def _objc_to_py(obj):
    """Recursively convert NSDictionary/NSArray/NSNumber/NSString to Python."""
    if isinstance(obj, dict) or hasattr(obj, "allKeys"):
        return {str(k): _objc_to_py(obj[k]) for k in obj}
    if isinstance(obj, (list, tuple)) or (
        hasattr(obj, "count") and hasattr(obj, "objectAtIndex_")
    ):
        return [_objc_to_py(x) for x in obj]
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    if hasattr(obj, "doubleValue"):
        value = obj.doubleValue()
        return int(value) if value == int(value) else value
    return str(obj)


def _make_handler_classes():
    """Define the ObjC helper classes exactly once (class names are global)."""
    global _handler_classes
    if _handler_classes is not None:
        return _handler_classes

    from Foundation import NSObject

    class SpeakeasyScriptHandler(NSObject):
        def initWithOwner_(self, owner):
            self = self.init()
            if self is None:
                return None
            self.owner = owner
            return self

        def userContentController_didReceiveScriptMessage_(self, controller, message):
            self.owner._on_message(_objc_to_py(message.body()))

    class SpeakeasyNavDelegate(NSObject):
        def initWithOwner_(self, owner):
            self = self.init()
            if self is None:
                return None
            self.owner = owner
            return self

        def webView_didFinishNavigation_(self, webview, navigation):
            self.owner._on_page_loaded()

    _handler_classes = (SpeakeasyScriptHandler, SpeakeasyNavDelegate)
    return _handler_classes


class WebWindow:
    def __init__(
        self,
        title: str,
        width: float,
        height: float,
        page: str,
        dispatcher: BridgeDispatcher,
    ) -> None:
        from AppKit import (
            NSAppearance,
            NSAppearanceNameDarkAqua,
            NSViewHeightSizable,
            NSViewWidthSizable,
        )
        from Foundation import NSMakeRect, NSURL
        from WebKit import WKWebView, WKWebViewConfiguration

        from speakeasy.ui import glass

        script_handler_cls, nav_delegate_cls = _make_handler_classes()

        self._dispatcher = dispatcher
        self._queue = EvalQueue()

        window, effect = glass.make_glass_window(title, width, height)
        window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self.window = window

        config = WKWebViewConfiguration.alloc().init()
        self._script_handler = script_handler_cls.alloc().initWithOwner_(self)
        config.userContentController().addScriptMessageHandler_name_(
            self._script_handler, "speakeasy"
        )

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, width, height), config
        )
        # Transparent webview: the NSVisualEffectView behind supplies the blur;
        # the page paints only its semi-transparent surfaces on top.
        webview.setValue_forKey_(False, "drawsBackground")
        webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._nav_delegate = nav_delegate_cls.alloc().initWithOwner_(self)
        webview.setNavigationDelegate_(self._nav_delegate)
        effect.addSubview_(webview)
        self._webview = webview

        dist = settings.frontend_dist_path()
        page_path = dist / f"{page}.html"
        if not page_path.is_file():
            raise FileNotFoundError(
                f"frontend page missing: {page_path} — run `npm --prefix frontend run build`"
            )
        page_url = NSURL.fileURLWithPath_(str(page_path))
        # Directory-level read access so code-split assets under dist/ resolve.
        root_url = NSURL.fileURLWithPath_isDirectory_(str(dist), True)
        webview.loadFileURL_allowingReadAccessToURL_(page_url, root_url)

    # -- main-thread API ---------------------------------------------------

    def show(self) -> None:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def eval_js(self, js: str) -> None:
        self._queue.send(js, self._flush)

    def emit(self, event: str, payload=None) -> None:
        self.eval_js(
            "window.speakeasyBridge && window.speakeasyBridge._emit("
            f"{json.dumps(event)}, {json.dumps(payload)})"
        )

    # -- callbacks from the ObjC helpers ------------------------------------

    def _on_message(self, message) -> None:
        self._dispatcher.dispatch(message, self.eval_js)

    def _on_page_loaded(self) -> None:
        self._queue.mark_ready(self._flush)

    def _flush(self, js: str) -> None:
        self._webview.evaluateJavaScript_completionHandler_(js, None)
