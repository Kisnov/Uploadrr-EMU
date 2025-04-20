from contextlib import contextmanager
from typing import Optional

from rich.console import Group
from rich.live import Live
from rich.spinner import Spinner

class ProgressManager:
    _instance: Optional["ProgressManager"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, console, quiet: bool = False, unattended: bool = False):
        if not hasattr(self, '_initialized'):  # Prevent re-initialization
            self.console = console
            self.quiet = quiet
            self.unattended = unattended
            self.live: Optional[Live] = None
            self.progress_widget = Spinner("dots", text="Initializing...", style="green1")
            self.progress_group = Group(self.progress_widget)
            self._initialized = True

    @classmethod
    def get_instance(cls) -> Optional["ProgressManager"]:
        return cls._instance

    def start(self, total_files: int) -> None:
        """Start the Live display."""
        if not self.live and (self.quiet or not self.unattended):
            self.live = Live(self.progress_group, console=self.console, transient=True, refresh_per_second=10)
            self.live.start()
            self.update(f"Ready to process {total_files} files")

    def stop(self) -> None:
        """Stop the Live display if running."""
        if self.live:
            self.live.stop()
            self.live = None

    def update(self, message: str) -> None:
        """Update the progress widget text."""
        self.progress_widget.text = message
        if self.live:
            self.live.update(self.progress_group)

    @contextmanager
    def pause_for_interaction(self):
        """Pause Live for user interaction if not unattended, then resume."""
        if self.live and not self.unattended and not self.quiet:
            self.live.stop()
        try:
            yield
        finally:
            if self.live and not self.unattended and not self.quiet:
                self.live.start()

    async def shutdown(self) -> None:
        """Clean up the Live instance."""
        self.stop()
