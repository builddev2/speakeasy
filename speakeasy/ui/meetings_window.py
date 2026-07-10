"""Meetings window: browse saved transcripts on a glass surface.

Sidebar lists meetings newest-first; the pane shows the selected transcript
with Copy / Export / Rename / Delete. Pure viewer — unlike the training
window it never touches the engine, so dictation (or an in-flight meeting)
keeps running while it's open. Transcripts are text-only JSON; Export writes
.txt or .md picked by the save panel's chosen extension.

Visual language matches main_window.py: a fixed dark glass surface (the
commissioned "Speakeasy Dock Window Redesign"), not adaptive to system
light/dark. The transcript is syntax-colored — timestamp, then each
speaker in a distinct hue, echoing the recording waveform's rainbow so a
transcript still visually "sounds like" a room of different voices.
"""

import re

import objc
from AppKit import (
    NSAppearance,
    NSAppearanceNameDarkAqua,
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSAttributedString,
    NSBezelBorder,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSMakeRange,
    NSModalResponseOK,
    NSMutableAttributedString,
    NSSavePanel,
    NSScrollView,
    NSTextAlignmentLeft,
    NSTextField,
    NSTextView,
    NSView,
)
from Foundation import NSMakeRect, NSObject

from .. import injector, meetings
from . import glass

_W, _H = 720, 480
_SIDEBAR_W = 232
_PANE_X = _SIDEBAR_W + 20
_PANE_W = _W - _PANE_X - 20
_ROW_H = 46


def _rgba(r, g, b, a):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r / 255, g / 255, b / 255, a)


def _hex(h, a=1.0):
    h = h.lstrip("#")
    return _rgba(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


_TEXT_PRIMARY = _rgba(255, 255, 255, 0.97)
_TEXT_META = _rgba(255, 255, 255, 0.50)
_TEXT_META_DIM = _rgba(255, 255, 255, 0.40)
_ROW_ACTIVE_BG = _rgba(235, 152, 93, 0.16)
_ROW_ACTIVE_BORDER = _rgba(235, 152, 93, 0.28)
_ROW_TITLE_ACTIVE = _rgba(255, 255, 255, 0.96)
_ROW_TITLE = _rgba(255, 255, 255, 0.62)
_ROW_SUB_ACTIVE = _rgba(255, 255, 255, 0.50)
_ROW_SUB = _rgba(255, 255, 255, 0.35)
_TRANSCRIPT_BG = _rgba(0, 0, 0, 0.30)
_TRANSCRIPT_BORDER = _rgba(255, 255, 255, 0.08)
_TIME_COLOR = _rgba(235, 152, 93, 0.72)
_TEXT_COLOR = _rgba(255, 255, 255, 0.82)
_CARD_BG = _rgba(255, 255, 255, 0.09)
_CARD_BORDER = _rgba(255, 255, 255, 0.15)
_CARD_TEXT = _rgba(255, 255, 255, 0.92)
_DELETE_BG = _rgba(255, 90, 78, 0.14)
_DELETE_BORDER = _rgba(255, 90, 78, 0.30)
_DELETE_TEXT = _hex("#FF8A80")

# Speaker palette, in first-appearance order — matches the design's sample.
_SPEAKER_COLORS = [
    _hex("#6fd8b0"), _hex("#F0B34A"), _hex("#C08CF2"), _hex("#6EA8FF"),
    _hex("#7ED97E"), _hex("#F0895A"), _hex("#DE8FB4"),
]
_SPEAKER_NUM_RE = re.compile(r"(\d+)")


def _speaker_color(speaker: str) -> NSColor:
    m = _SPEAKER_NUM_RE.search(speaker)
    idx = (int(m.group(1)) - 1) if m else 0
    return _SPEAKER_COLORS[idx % len(_SPEAKER_COLORS)]


def _titled(button, text, size, weight, color) -> None:
    font = NSFont.systemFontOfSize_weight_(size, weight)
    button.setAttributedTitle_(
        NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font, NSForegroundColorAttributeName: color}
        )
    )


def _rounded(view, radius, bg=None, border=None) -> None:
    view.setWantsLayer_(True)
    layer = view.layer()
    layer.setCornerRadius_(radius)
    if bg is not None:
        layer.setBackgroundColor_(bg.CGColor())
    if border is not None:
        layer.setBorderWidth_(0.5)
        layer.setBorderColor_(border.CGColor())


def _glass_button(text, target, action) -> NSButton:
    button = glass.ClickyButton.buttonWithTitle_target_action_(text, target, action)
    button.setBordered_(False)
    _rounded(button, 9.0, _CARD_BG, _CARD_BORDER)
    _titled(button, text, 13, NSFontWeightSemibold, _CARD_TEXT)
    return button


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
        window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self._window = window

        sidebar = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, _SIDEBAR_W, _H))
        _rounded(sidebar, 0.0, _rgba(255, 255, 255, 0.02))
        sidebar.layer().setBorderWidth_(0)
        content.addSubview_(sidebar)
        divider = NSView.alloc().initWithFrame_(NSMakeRect(_SIDEBAR_W - 1, 0, 1, _H))
        divider.setWantsLayer_(True)
        divider.layer().setBackgroundColor_(_rgba(255, 255, 255, 0.07).CGColor())
        content.addSubview_(divider)

        header = glass.make_label("MEETINGS", size=11, weight=NSFontWeightSemibold)
        header.setTextColor_(_TEXT_META_DIM)
        header.setFrame_(NSMakeRect(16, _H - 30, _SIDEBAR_W - 32, 16))
        sidebar.addSubview_(header)

        # The list can outgrow the window, so rows live in a scroll view
        # (the training sidebar's fixed buttons don't, its session count is
        # static).
        self._list_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 12, _SIDEBAR_W, _H - 56)
        )
        self._list_scroll.setDrawsBackground_(False)
        self._list_scroll.setHasVerticalScroller_(True)
        self._list_doc = _FlippedView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _SIDEBAR_W, 0)
        )
        self._list_scroll.setDocumentView_(self._list_doc)
        sidebar.addSubview_(self._list_scroll)

        self._title = glass.make_label("", size=19, weight=NSFontWeightSemibold)
        self._title.setTextColor_(_TEXT_PRIMARY)
        self._title.setFrame_(NSMakeRect(_PANE_X, _H - 46, _PANE_W, 24))
        content.addSubview_(self._title)

        self._meta = glass.make_label("", size=12.5, weight=NSFontWeightRegular)
        self._meta.setTextColor_(_TEXT_META)
        self._meta.setFrame_(NSMakeRect(_PANE_X, _H - 68, _PANE_W, 16))
        content.addSubview_(self._meta)

        transcript_box = NSView.alloc().initWithFrame_(
            NSMakeRect(_PANE_X, 62, _PANE_W, _H - 148)
        )
        _rounded(transcript_box, 12.0, _TRANSCRIPT_BG, _TRANSCRIPT_BORDER)
        content.addSubview_(transcript_box)

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(6, 6, _PANE_W - 12, _H - 148 - 12)
        )
        scroll.setBorderType_(NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        self._text = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _PANE_W - 12, _H - 148 - 12)
        )
        self._text.setEditable_(False)
        self._text.setDrawsBackground_(False)
        scroll.setDocumentView_(self._text)
        transcript_box.addSubview_(scroll)

        self._buttons = []
        for i, (label, action) in enumerate(
            [
                ("Copy", b"copyTranscript:"),
                ("Export…", b"exportTranscript:"),
                ("Rename…", b"renameMeeting:"),
            ]
        ):
            button = _glass_button(label, self, action)
            button.setFrame_(NSMakeRect(_PANE_X + i * 108, 17, 100, 34))
            content.addSubview_(button)
            self._buttons.append(button)

        delete_btn = glass.ClickyButton.buttonWithTitle_target_action_(
            "Delete", self, b"deleteMeeting:"
        )
        delete_btn.setBordered_(False)
        _rounded(delete_btn, 9.0, _DELETE_BG, _DELETE_BORDER)
        _titled(delete_btn, "Delete", 13, NSFontWeightSemibold, _DELETE_TEXT)
        delete_btn.setFrame_(NSMakeRect(_W - 20 - 100, 17, 100, 34))
        content.addSubview_(delete_btn)
        self._buttons.append(delete_btn)

        self._empty = glass.make_label(
            "No meetings yet — use “Begin Meeting” in the menu bar.",
            size=13,
        )
        self._empty.setTextColor_(_TEXT_META)
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
        # Keep the previous selection if it survived, else newest, else none.
        self._selected = None
        if self._meetings:
            self._selected = self._meetings[0]
            if selected_id:
                for meeting in self._meetings:
                    if meeting.meeting_id == selected_id:
                        self._selected = meeting
                        break
        for i, meeting in enumerate(self._meetings):
            self._list_doc.addSubview_(
                self._make_row(meeting, i, active=meeting is self._selected)
            )
        self._show_selected()

    @objc.python_method
    def _make_row(self, meeting, index, active: bool) -> NSButton:
        minutes = int(meeting.duration_seconds) // 60
        speakers = len({s.speaker for s in meeting.segments})
        subtitle = f"{minutes} min · {speakers} speaker(s)"

        button = glass.ClickyButton.buttonWithTitle_target_action_("", self, b"selectMeeting:")
        button.setTag_(index)
        button.setBordered_(False)
        button.setFrame_(NSMakeRect(8, index * _ROW_H + 2, _SIDEBAR_W - 16, _ROW_H - 4))
        if active:
            _rounded(button, 8.0, _ROW_ACTIVE_BG, _ROW_ACTIVE_BORDER)
            title_color, sub_color = _ROW_TITLE_ACTIVE, _ROW_SUB_ACTIVE
        else:
            title_color, sub_color = _ROW_TITLE, _ROW_SUB

        text = NSMutableAttributedString.alloc().init()
        text.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                meeting.title + "\n",
                {
                    NSFontAttributeName: NSFont.systemFontOfSize_weight_(
                        13.5, NSFontWeightSemibold if active else NSFontWeightRegular
                    ),
                    NSForegroundColorAttributeName: title_color,
                },
            )
        )
        text.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                subtitle,
                {
                    NSFontAttributeName: NSFont.systemFontOfSize_weight_(11.5, NSFontWeightRegular),
                    NSForegroundColorAttributeName: sub_color,
                },
            )
        )
        button.setAttributedTitle_(text)
        button.cell().setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
        button.setAlignment_(NSTextAlignmentLeft)
        return button

    @objc.python_method
    def _show_selected(self):
        meeting = self._selected
        has = meeting is not None
        self._empty.setHidden_(has)
        for button in self._buttons:
            button.setHidden_(not has)
        if not has:
            self._title.setStringValue_("")
            self._meta.setStringValue_("")
            self._text.setString_("")
            return
        self._title.setStringValue_(meeting.title)
        minutes = int(meeting.duration_seconds) // 60
        speakers = len({s.speaker for s in meeting.segments})
        self._meta.setStringValue_(
            f"{meeting.created}   •   {minutes} min   •   {speakers} speaker(s)"
        )
        self._text.setString_("")
        self._text.textStorage().setAttributedString_(
            _render_attributed_transcript(meeting)
        )
        self._text.scrollRangeToVisible_(NSMakeRange(0, 0))

    # -- actions ------------------------------------------------------------

    def selectMeeting_(self, sender):
        index = sender.tag()
        if 0 <= index < len(self._meetings):
            self._selected = self._meetings[index]
            for view in self._list_doc.subviews():
                view.removeFromSuperview()
            for i, meeting in enumerate(self._meetings):
                self._list_doc.addSubview_(
                    self._make_row(meeting, i, active=meeting is self._selected)
                )
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


def _render_attributed_transcript(meeting) -> NSAttributedString:
    """[time] Speaker N: text — time in coral, speaker bold in its hue,
    body in soft white, monospace throughout."""
    mono = lambda size, weight=NSFontWeightRegular: NSFont.monospacedSystemFontOfSize_weight_(size, weight)
    out = NSMutableAttributedString.alloc().init()
    if not meeting.segments:
        out.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                "(no speech detected)",
                {NSFontAttributeName: mono(12), NSForegroundColorAttributeName: _TEXT_META},
            )
        )
        return out
    for i, seg in enumerate(meeting.segments):
        if i:
            out.appendAttributedString_(NSAttributedString.alloc().initWithString_("\n"))
        out.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                f"[{meetings._timestamp(seg.start)}] ",
                {NSFontAttributeName: mono(12), NSForegroundColorAttributeName: _TIME_COLOR},
            )
        )
        out.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                f"{seg.speaker}: ",
                {
                    NSFontAttributeName: mono(12, NSFontWeightBold),
                    NSForegroundColorAttributeName: _speaker_color(seg.speaker),
                },
            )
        )
        out.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                seg.text,
                {NSFontAttributeName: mono(12), NSForegroundColorAttributeName: _TEXT_COLOR},
            )
        )
    return out
