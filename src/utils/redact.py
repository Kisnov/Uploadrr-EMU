import re
from typing import Any, Dict, List, Optional, Tuple

from rich.console import RenderResult
from rich.logging import RichHandler
from rich.traceback import Traceback


class Redactrr:
    def __init__(self, unredact: bool = False):
        self.unredact = unredact
        self._compiled: List[Tuple[re.Pattern, str]] = []

    def load_from_config(self, config: Dict[str, Any]) -> None:
        """Inspect config dict and register sensitive keys automatically."""
        sensitive_keys = {  # TODO refine this!!
            "tmdb_api",
            "api_key",
            "password",
            "pass",
            "token",
            "pid",
            "user",
            "username",
            "announce_key",
            "annouce_url",
            "ptscreens_api",
        }
        placeholders = {}

        def _walk(d: Any, path: str = ""):
            if isinstance(d, dict):
                for k, v in d.items():
                    full_key = f"{path}.{k}" if path else k
                    if any(sens in k.lower() for sens in sensitive_keys):
                        placeholders[str(v)] = k.upper()
                    _walk(v, full_key)
            elif isinstance(d, (list, tuple)):
                for i, v in enumerate(d):
                    _walk(v, f"{path}[{i}]")

        _walk(config)

        for secret, key in placeholders.items():
            if secret and isinstance(secret, str) and secret.strip():
                self.add_rule(secret, key)

    def add_rule(self, value: str, placeholder: Optional[str] = None) -> None:
        """Register a new value for redaction."""
        if not value or not isinstance(value, str) or not value.strip():
            return
        ph = f"<{placeholder or 'REDACTED'}>"
        self._compiled.append((re.compile(r"\b" + re.escape(value) + r"\b", re.IGNORECASE), ph))

    def redact(self, text: str) -> str:
        """Apply redaction to text if needed."""
        if self.unredact or not self._compiled or not isinstance(text, str):
            return text
        for pattern, placeholder in self._compiled:
            text = pattern.sub(placeholder, text)
        return text


class RedactingRichHandler(RichHandler):
    def __init__(self, *args, redactor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.redactor = redactor

    def emit(self, record):
        if self.redactor:
            record.msg = self.redactor.redact(str(record.msg))
            if record.exc_text:
                record.exc_text = self.redactor.redact(record.exc_text)
            if record.args:
                record.args = tuple(self.redactor.redact(str(a)) for a in record.args)
        return super().emit(record)

    def render_traceback(self, traceback: Traceback) -> RenderResult:
        """Override to redact paths in tracebacks."""
        for frame in traceback.frames:
            frame.filename = self.redactor.redact(frame.filename)
            if frame.line is not None:
                frame.line = self.redactor.redact(frame.line)
            if frame.locals:
                for key in list(frame.locals):
                    frame.locals[key] = self.redactor.redact(frame.locals[key])
        return super().render_traceback(traceback)
