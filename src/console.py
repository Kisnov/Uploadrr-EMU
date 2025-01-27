from rich.console import Console
from rich.theme import Theme
import logging
from rich.logging import RichHandler

# Define log colors and theme
log_colors = Theme({
    'logging.level.debug': 'bold blue',
    'logging.level.info': 'green',
    'logging.level.warning': 'bold yellow',
    'logging.level.error': 'bold red',
    'logging.level.critical': 'bold bright_white on red',
})

# Initialize the console with the logging theme
console = Console(theme=log_colors)

# Set up logging with RichHandler
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, show_time=False, markup=True, console=console)]
)

# Get a logger instance
log = logging.getLogger(__name__)


def set_log_level(debug=False):
    level = logging.DEBUG if debug else logging.INFO

    # Update the root logger level
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Update the level for all handlers of the root logger
    for handler in root_logger.handlers:
        handler.setLevel(level)

    # Ensure all other loggers respect the new level
    for logger_name in logging.root.manager.loggerDict:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = True  # Ensure messages bubble up to the root logger
        for handler in logger.handlers:
            handler.setLevel(level)

    # Ensure RichHandler is updated
    for handler in root_logger.handlers:
        if isinstance(handler, RichHandler):
            handler.setLevel(level)
