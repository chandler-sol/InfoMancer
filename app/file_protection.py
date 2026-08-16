from __future__ import annotations


class MediaWriteBlocked(RuntimeError):
    pass


class FileProtectionService:
    """One server-side gate for operations that mutate user media files."""

    def __init__(self, app_settings) -> None:
        self.app_settings = app_settings

    @property
    def mode(self) -> str:
        return self.app_settings.file_protection_mode()

    @property
    def media_writes_allowed(self) -> bool:
        return self.mode != "readonly"

    @property
    def automatic_permanent_delete_allowed(self) -> bool:
        return self.mode == "standard"

    def require_media_write(self, action: str = "change media files") -> None:
        if self.media_writes_allowed:
            return
        raise MediaWriteBlocked(
            f"Read-Only Mode is active. InfoMancer can scan, inspect, match, and review media, "
            f"but it will not {action}. Switch File Protection Mode to Standard or Lockdown "
            "before making filesystem changes."
        )
