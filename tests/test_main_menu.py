"""The main menu: app menu with Quit, plus an Edit menu whose actions go to
the first responder (target nil) so Cmd+A/C/V/X/Z work in WKWebView fields."""

from AppKit import (
    NSEventModifierFlagCommand,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
)
from Foundation import NSObject

from speakeasy.ui.menubar import _build_main_menu

CMD = NSEventModifierFlagCommand
CMD_OPT_SHIFT = (
    NSEventModifierFlagCommand | NSEventModifierFlagOption | NSEventModifierFlagShift
)


class _Target(NSObject):
    def quitApp_(self, sender):
        pass


def _menu():
    target = _Target.alloc().init()
    return target, _build_main_menu(target)


def test_top_level_items_are_app_then_edit():
    _, menu = _menu()
    assert menu.numberOfItems() == 2
    assert menu.itemAtIndex_(1).submenu().title() == "Edit"


def test_app_menu_still_has_quit_targeted_at_target():
    target, menu = _menu()
    app_menu = menu.itemAtIndex_(0).submenu()
    quit_item = app_menu.itemAtIndex_(0)
    assert quit_item.action() == "quitApp:"
    assert quit_item.keyEquivalent() == "q"
    assert quit_item.target() is target


def test_edit_menu_items_in_order_with_nil_targets():
    _, menu = _menu()
    edit = menu.itemAtIndex_(1).submenu()
    expected = [
        ("Undo", "undo:", "z", CMD),
        ("Redo", "redo:", "Z", CMD),
        None,
        ("Cut", "cut:", "x", CMD),
        ("Copy", "copy:", "c", CMD),
        ("Paste", "paste:", "v", CMD),
        ("Paste and Match Style", "pasteAsPlainText:", "V", CMD_OPT_SHIFT),
        ("Delete", "delete:", "", CMD),
        ("Select All", "selectAll:", "a", CMD),
    ]
    assert edit.numberOfItems() == len(expected)
    for index, want in enumerate(expected):
        item = edit.itemAtIndex_(index)
        if want is None:
            assert item.isSeparatorItem()
            continue
        title, action, key, mask = want
        assert item.title() == title
        assert item.action() == action
        assert item.keyEquivalent() == key
        assert item.target() is None
        assert item.keyEquivalentModifierMask() == mask
