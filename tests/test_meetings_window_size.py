from speakeasy.ui.meetings_window import default_content_size


def test_default_size_is_ninety_percent_of_visible_screen():
    # 14" MacBook Pro default visible frame (menu bar and Dock removed).
    assert default_content_size(1512, 944) == (1361, 850)


def test_default_size_never_below_minimum():
    assert default_content_size(800, 500) == (820, 520)
