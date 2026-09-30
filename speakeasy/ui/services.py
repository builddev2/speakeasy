"""Process-wide handles set once by menubar at launch, so window controllers
built from several places (menubar, the Dock) share them. None in tests and
in the MCP server, which must never touch EventKit."""

engine = None          # DictationEngine
calendar_sync = None   # calendar_sync.CalendarSync
