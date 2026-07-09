"""Meetings window: browse saved transcripts on a glass surface.

Sidebar lists meetings newest-first; the pane shows the selected transcript
with Copy / Export / Rename / Delete. Pure viewer — unlike the training
window it never touches the engine, so dictation (or an in-flight meeting)
keeps running while it's open. Transcripts are text-only JSON; Export writes
.txt or .md picked by the save panel's chosen extension.
"""

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSBezelBorder,
    NSButton,
    NSFont,
    NSFontWeightSemibold,
    NSMakeRange,
    NSModalResponseOK,
    NSSavePanel,
    NSScrollView,
    NSTextAlignmentLeft,
    NSTextField,
    NSTextView,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialSidebar,
    NSVisualEffectStateActive,
    NSVisualEffectView,
)
from Foundation import NSMakeRect, NSObject

from .. import injector, meetings
from . import glass

_W, _H = 680, 440
_SIDEBAR_W = 220
_PANE_X = _SIDEBAR_W + 20
_PANE_W = _W - _PANE_X - 20
_ROW_H = 30


class _FlippedView(NSView):
    """Top-down coordinates so meeting rows stack from the top."""

    def isFlipped(self):
        return True


class MeetingsWindowController(NSObject):
    """Owns the Meetings window. Main-thread only."""

    def init(self):
        self = objc.super(MeetingsWindowController, self).init()
        if self is None:
            return None
        self._meetings = []
        self._selected = None
        self._build_window()
        return self

    # -- layout -----------------------------------------------------------

    @objc.python_method
    def _build_window(self):
        window, content = glass.make_glass_window("Meetings", _W, _H)
        window.setDelegate_(self)
        self._window = window

        sidebar = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _SIDEBAR_W, _H)
        )
        sidebar.setMaterial_(NSVisualEffectMaterialSidebar)
        sidebar.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        sidebar.setState_(NSVisualEffectStateActive)
        content.addSubview_(sidebar)

        header = glass.make_label(
            "MEETINGS", size=11, weight=NSFontWeightSemibold, secondary=True
        )
        header.setFrame_(NSMakeRect(16, _H - 58, _SIDEBAR_W - 32, 16))
        sidebar.addSubview_(header)

        # The list can outgrow the window, so rows live in a scroll view
        # (the training sidebar's fixed buttons don't, its session count is
        # static).
        self._list_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 12, _SIDEBAR_W, _H - 78)
        )
        self._list_scroll.setDrawsBackground_(False)
        self._list_scroll.setHasVerticalScroller_(True)
        self._list_doc = _FlippedView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _SIDEBAR_W, 0)
        )
        self._list_scroll.setDocumentView_(self._list_doc)
        sidebar.addSubview_(self._list_scroll)

        self._title = glass.make_label("", size=17, weight=NSFontWeightSemibold)
        self._title.setFrame_(NSMakeRect(_PANE_X, _H - 62, _PANE_W, 22))
        content.addSubview_(self._title)

        self._meta = glass.make_label("", size=12, secondary=True)
        self._meta.setFrame_(NSMakeRect(_PANE_X, _H - 84, _PANE_W, 16))
        content.addSubview_(self._meta)

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(_PANE_X, 60, _PANE_W, _H - 156)
        )
        scroll.setBorderType_(NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        self._text = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _PANE_W, _H - 156)
        )
        self._text.setEditable_(False)
        self._text.setDrawsBackground_(False)
        self._text.setFont_(
            NSFont.monospacedSystemFontOfSize_weight_(11.5, 0.0)
        )
        scroll.setDocumentView_(self._text)
        content.addSubview_(scroll)

        self._buttons = []
        for i, (label, action) in enumerate(
            [
                ("Copy", b"copyTranscript:"),
                ("Export…", b"exportTranscript:"),
                ("Rename…", b"renameMeeting:"),
                ("Delete", b"deleteMeeting:"),
            ]
        ):
            button = NSButton.buttonWithTitle_target_action_(label, self, action)
            button.setFrame_(NSMakeRect(_PANE_X + i * 108, 17, 100, 30))
            content.addSubview_(button)
            self._buttons.append(button)

        self._empty = glass.make_label(
            "No meetings yet — use “Begin Meeting” in the menu bar.",
            size=13,
            secondary=True,
        )
        self._empty.setFrame_(NSMakeRect(_PANE_X, _H / 2, _PANE_W, 20))
        content.addSubview_(self._empty)

    # -- open / reload ------------------------------------------------------

    def show(self):
        self.reload()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    def reload(self):
        """Re-list from disk and rebuild the sidebar; called on open and
        whenever a meeting is saved/renamed/deleted."""
        selected_id = self._selected.meeting_id if self._selected else None
        self._meetings = meetings.list_meetings()
        for view in list(self._list_doc.subviews()):
            view.removeFromSuperview()
        height = max(len(self._meetings) * _ROW_H, 1)
        self._list_doc.setFrame_(NSMakeRect(0, 0, _SIDEBAR_W, height))
        for i, meeting in enumerate(self._meetings):
            button = NSButton.buttonWithTitle_target_action_(
                meeting.title, self, b"selectMeeting:"
            )
            button.setTag_(i)
            button.setBordered_(False)
            button.setAlignment_(NSTextAlignmentLeft)
            button.setFrame_(
                NSMakeRect(12, i * _ROW_H, _SIDEBAR_W - 24, _ROW_H - 6)
            )
            self._list_doc.addSubview_(button)
        # Keep the previous selection if it survived, else newest, else none.
        self._selected = None
        if self._meetings:
            self._selected = self._meetings[0]
            if selected_id:
                for meeting in self._meetings:
                    if meeting.meeting_id == selected_id:
                        self._selected = meeting
                        break
        self._show_selected()

    @objc.python_method
    def _show_selected(self):
        meeting = self._selected
        has = meeting is not None
        self._empty.setHidden_(has)
        for button in self._buttons:
            button.setEnabled_(has)
        if not has:
            self._title.setStringValue_("")
            self._meta.setStringValue_("")
            self._text.setString_("")
            return
        self._title.setStringValue_(meeting.title)
        minutes = int(meeting.duration_seconds) // 60
        speakers = len({s.speaker for s in meeting.segments})
        self._meta.setStringValue_(
            f"{meeting.created}   ·   {minutes} min   ·   {speakers} speaker(s)"
        )
        body = "\n".join(
            f"[{meetings._timestamp(s.start)}] {s.speaker}: {s.text}"
            for s in meeting.segments
        ) or "(no speech detected)"
        self._text.setString_(body)
        self._text.scrollRangeToVisible_(NSMakeRange(0, 0))

    # -- actions ------------------------------------------------------------

    def selectMeeting_(self, sender):
        index = sender.tag()
        if 0 <= index < len(self._meetings):
            self._selected = self._meetings[index]
            self._show_selected()

    def copyTranscript_(self, sender):
        if self._selected is not None:
            injector.set_clipboard(meetings.render_txt(self._selected))

    def exportTranscript_(self, sender):
        meeting = self._selected
        if meeting is None:
            return
        panel = NSSavePanel.savePanel()
        # Deprecated in favor of UTType, but functional and avoids pinning a
        # whole extra pyobjc framework for two extensions.
        panel.setAllowedFileTypes_(["txt", "md"])
        panel.setNameFieldStringValue_(f"{meeting.title}.txt")

        def completion(response):
            if response != NSModalResponseOK:
                return
            path = panel.URL().path()
            render = meetings.render_md if str(path).endswith(".md") else meetings.render_txt
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(render(meeting))
            except OSError as err:
                self._error("Couldn't export the transcript", str(err))

        panel.beginSheetModalForWindow_completionHandler_(self._window, completion)

    def renameMeeting_(self, sender):
        meeting = self._selected
        if meeting is None:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Rename Meeting")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 260, 24))
        field.setStringValue_(meeting.title)
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
        alert.addButtonWithTitle_("Rename")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        try:
            meeting.rename(str(field.stringValue()))
        except (OSError, ValueError) as err:
            self._error("Couldn't rename the meeting", str(err))
        self.reload()

    def deleteMeeting_(self, sender):
        meeting = self._selected
        if meeting is None:
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"Delete “{meeting.title}”?")
        alert.setInformativeText_(
            "The transcript is deleted permanently — there is no recording "
            "to recover it from."
        )
        alert.addButtonWithTitle_("Delete")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        meeting.delete()
        self._selected = None
        self.reload()

    # -- helpers --------------------------------------------------------------

    @objc.python_method
    def _error(self, message: str, detail: str):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.setInformativeText_(detail)
        alert.runModal()
