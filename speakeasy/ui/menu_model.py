def menu_flags(state: str, pending: int = 0) -> dict:
    return {"next_up": state == "ready",
            "meeting_notes": state in ("meeting_recording", "meeting_processing"),
            "retry_mic": state == "mic_failed",
            "cancel": pending > 0}


def start_enabled(state: str) -> bool:
    # Earlier meetings may still be processing; only a recording blocks a new one.
    return state in ("ready", "meeting_processing")


def processing_suffix(pending: int) -> str:
    if pending < 1:
        return ""
    return " · processing 1 meeting" if pending == 1 else f" · processing {pending} meetings"


def quit_warning(recording: bool, pending: int) -> str | None:
    if recording:
        if pending < 1:
            return "The current recording will be discarded."
        noun = "processing meeting" if pending == 1 else "processing meetings"
        return f"The current recording and {pending} {noun} will be discarded."
    if pending < 1:
        return None
    if pending == 1:
        return "1 meeting is still processing. Quitting discards it."
    return f"{pending} meetings are still processing. Quitting discards them."
