import re
from typing import Any, Dict, List, Optional, Tuple

from rich.console import RenderResult, group
from rich.logging import RichHandler
from rich.traceback import Traceback


class Redactrr:
    def __init__(self, unredact: bool = False):
        self.unredact = unredact
        self._compiled: List[Tuple[re.Pattern, str]] = []

    def load_from_config(self, config: Dict[str, Any]) -> None:
        """Inspect config dict and register sensitive keys or entire sections automatically."""
        sensitive_keys = {"tmdb", "api_key", "password", "pass", "token", "pid", "announce_key", "annouce_url"}
        sensitive_paths = {"SCREENS.API_KEYS", "ADULT.API_KEYS"}
        placeholders = {}

        def _walk(d: Any, path: str = ""):
            if isinstance(d, dict):
                for k, v in d.items():
                    full_key = f"{path}.{k}" if path else k

                    if any(sens in k.lower() for sens in sensitive_keys):
                        if v is not None and isinstance(v, str) and v.strip():
                            placeholders[v] = k.upper()

                    if any(full_key.startswith(sens_path) for sens_path in sensitive_paths):
                        if v is not None and isinstance(v, str) and v.strip():
                            label = full_key.upper().replace(".", "_")
                            placeholders[v] = label

                    _walk(v, full_key)

            elif isinstance(d, (list, tuple)):
                for i, v in enumerate(d):
                    _walk(v, f"{path}[{i}]")

        _walk(config)

        for secret, key in placeholders.items():
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
    # NOTE this class was insuffecient. Unable to redact paths due to needing the filename to read sourcecode.
    def __init__(self, *args, redactor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.redactor = redactor

    def emit(self, record):
        if self.redactor:
            record.msg = self.redactor.redact(str(record.msg))
            if record.exc_text:
                record.exc_text = self.redactor.redact(record.exc_text)
            if record.args:
                new_args = []
                for a in record.args:
                    if isinstance(a, str):
                        new_args.append(self.redactor.redact(a))
                    else:
                        new_args.append(a)
                record.args = tuple(new_args)
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


@group()
def _redacting_render_stack(self, stack):
    """Wrapper around _render_stack that redacts displayed filenames."""
    import linecache
    import os

    from rich.columns import Columns
    from rich.highlighter import RegexHighlighter
    from rich.scope import render_scope
    from rich.syntax import Syntax
    from rich.text import Text
    from rich.traceback import _iter_syntax_lines

    class PathHighlighter(RegexHighlighter):
        highlights = [r"(?P<dim>.*/)(?P<bold>.+)"]

    def render_locals(frame):
        if frame.locals:
            yield render_scope(
                frame.locals,
                title="locals",
                indent_guides=self.indent_guides,
                max_length=self.locals_max_length,
                max_string=self.locals_max_string,
            )

    exclude_frames = None
    if self.max_frames != 0:
        exclude_frames = range(self.max_frames // 2, len(stack.frames) - self.max_frames // 2)

    excluded = False
    redactor = getattr(self, "redactor", None)

    for frame_index, frame in enumerate(stack.frames):
        if exclude_frames and frame_index in exclude_frames:
            excluded = True
            continue

        if excluded:
            assert exclude_frames is not None
            yield Text(f"\n... {len(exclude_frames)} frames hidden ...", justify="center", style="traceback.error")
            excluded = False

        first = frame_index == 0
        frame_filename = frame.filename
        suppressed = any(frame_filename.startswith(path) for path in self.suppress)

        # Redacting Filepath
        display_filename = frame_filename
        if redactor and not redactor.unredact:
            display_filename = redactor.redact(frame_filename)

        path_highlighter = PathHighlighter()
        if os.path.exists(frame.filename):
            text = Text.assemble(
                path_highlighter(Text(display_filename, style="pygments.string")),
                (":", "pygments.text"),
                (str(frame.lineno), "pygments.number"),
                " in ",
                (frame.name, "pygments.function"),
                style="pygments.text",
            )
        else:
            text = Text.assemble(
                "in ",
                (frame.name, "pygments.function"),
                (":", "pygments.text"),
                (str(frame.lineno), "pygments.number"),
                style="pygments.text",
            )

        if not frame.filename.startswith("<") and not first:
            yield ""
        yield text

        if frame.filename.startswith("<"):
            yield from render_locals(frame)
            continue

        if not suppressed:
            try:
                code_lines = linecache.getlines(frame.filename)
                code = "".join(code_lines)
                if not code:
                    continue

                lexer_name = self._guess_lexer(frame.filename, code)
                syntax = Syntax(
                    code,
                    lexer_name,
                    theme=self.theme,
                    line_numbers=True,
                    line_range=(frame.lineno - self.extra_lines, frame.lineno + self.extra_lines),
                    highlight_lines={frame.lineno},
                    word_wrap=self.word_wrap,
                    code_width=self.code_width,
                    indent_guides=self.indent_guides,
                    dedent=False,
                )
                yield ""
            except Exception as error:
                yield Text.assemble((f"\n{error}", "traceback.error"))
            else:
                if frame.last_instruction is not None:
                    start, end = frame.last_instruction
                    for line1, column1, column2 in _iter_syntax_lines(start, end):
                        try:
                            if column1 == 0:
                                line = code_lines[line1 - 1]
                                column1 = len(line) - len(line.lstrip())
                            if column2 == -1:
                                column2 = len(code_lines[line1 - 1])
                        except IndexError:
                            continue
                        syntax.stylize_range(style="traceback.error_range", start=(line1, column1), end=(line1, column2))
                yield (Columns([syntax, *render_locals(frame)], padding=1) if frame.locals else syntax)
