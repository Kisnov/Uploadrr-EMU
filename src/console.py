from rich.console import Console
from rich.theme import Theme
import logging
from rich.logging import RichHandler

log_colors = Theme({
    'logging.level.ddebug': 'bold magenta',
    'logging.level.debug': 'bold blue',
    'logging.level.info': 'green',
    'logging.level.warning': 'bold yellow',
    'logging.level.error': 'bold red',
    'logging.level.critical': 'bold bright_white on red',
})

console = Console(theme=log_colors)
quiet_mode = False
_original_print = console.print

def _quiet_aware_print(*args, force=False, **kwargs):
    if not quiet_mode or force:
        _original_print(*args, **kwargs)

console.print = _quiet_aware_print

# Custom log levels
logging.UPLOADRR_DEBUG = 10
logging.DDEBUG = 5
logging.addLevelName(logging.UPLOADRR_DEBUG, 'DEBUG')
logging.addLevelName(logging.DDEBUG, 'DDEBUG')

# Custom debug methods
def uploadrr_debug(self, message, *args, **kws):
    if self.isEnabledFor(logging.UPLOADRR_DEBUG):
        self._log(logging.UPLOADRR_DEBUG, message, args, **kws, stacklevel=2)

def ddebug(self, message, *args, **kws):
    if self.isEnabledFor(logging.DDEBUG):
        self._log(logging.DDEBUG, message, args, **kws, stacklevel=3)

logging.Logger.debug = uploadrr_debug
logging.Logger.ddebug = ddebug

# Get a logger instance and configure it
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
log.handlers.clear()
log.addHandler(RichHandler(rich_tracebacks=True, show_time=False, markup=True, console=console))
log.propagate = False

# Override logging methods to respect quiet mode
_original_log = logging.Logger._log

def _quiet_aware_log(self, level, msg, args, exc_info=None, extra=None, stack_info=False, stacklevel=2):
    if not quiet_mode or level >= logging.ERROR:  # Always show errors
        _original_log(self, level, msg, args, exc_info=exc_info, extra=extra, stack_info=stack_info, stacklevel=stacklevel)

logging.Logger._log = _quiet_aware_log

def set_log_level(debug=False, ddebug=False, quiet=False):
    """Sets log level and quiet mode dynamically."""
    global quiet_mode
    quiet_mode = quiet

    if ddebug:
        level = logging.DDEBUG
        root_level = logging.DEBUG
        log.propagate = True
    elif debug:
        level = logging.UPLOADRR_DEBUG
        root_level = logging.WARNING
        log.propagate = False
    else:
        level = logging.INFO
        root_level = logging.WARNING
        log.propagate = False

    log.setLevel(level)
    for handler in log.handlers:
        handler.setLevel(level)

    logging.getLogger().setLevel(root_level)
    root_logger = logging.getLogger()
    if not any(isinstance(h, RichHandler) for h in root_logger.handlers):
        root_logger.addHandler(RichHandler(rich_tracebacks=True, show_time=False, markup=True, console=console))

def force_print(*args, **kwargs):
    """Print regardless of quiet mode."""
    _original_print(*args, **kwargs)