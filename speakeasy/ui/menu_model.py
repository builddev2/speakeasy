def menu_flags(state: str) -> dict:
    return {"next_up": state == "ready",
            "meeting_notes": state in ("meeting_recording", "meeting_processing"),
            "retry_mic": state == "mic_failed",
            "cancel": state == "meeting_processing"}
