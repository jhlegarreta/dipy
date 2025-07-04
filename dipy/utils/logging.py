import logging
import sys


class CustomHandler(logging.Handler):
    """Custom logging handler that writes an empty line for empty log messages,
    otherwise formats the message as usual.
    """

    def __init__(self, stream=None, filename=None):
        if filename is not None:
            self.stream = open(filename, "a")
            super().__init__()
            self._should_close = True
        else:
            self.stream = stream if stream is not None else sys.stdout
            super().__init__()
            self._should_close = False

    def emit(self, record):
        try:
            msg = record.getMessage()
            if msg == "":
                # Directly write an empty line to the log stream
                self.stream.write("\n")
                self.flush()
            else:
                # Use the default formatter for non-empty messages
                formatted = self.format(record)
                self.stream.write(formatted + "\n")
                self.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        if getattr(self, "_should_close", False):
            try:
                self.stream.close()
            except Exception:
                pass
        super().close()


def get_logger(name="dipy", filename=None):
    """Return a logger instance configured for DIPY.

    Parameters
    ----------
    name : str
        The logger name.
    filename : str, Path or None, optional
        If provided, log messages will also be saved to this file. If ``None``,
        logs are sent to stdout.

    Returns
    -------
    logger : logging.Logger
        Configured logger.
    """

    logger = logging.getLogger(name)
    if not logger.hasHandlers():
        if filename:
            handler = CustomHandler(filename=filename)
        else:
            handler = CustomHandler(stream=sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def configure_logger(
    level=logging.INFO,
    fmt="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    filename=None,
):
    """Reconfigure DIPY logger.

    Parameters
    ----------
    level : int
        Logging level (e.g., logging.INFO).
    fmt : str, optional
        Log message format.
    datefmt : str, optional
        Date format for log messages.
    filename : str, Path or None, optional
        If provided, log messages will also be saved to this file. If ``None``,
        logs are sent to stdout.
    """

    # Remove all handlers associated with the root logger object.
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    if filename:
        handler = CustomHandler(filename=filename)
    else:
        handler = CustomHandler(stream=sys.stdout)

    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logging.root.addHandler(handler)
    logging.root.setLevel(level)


# Provide a default logger for convenience
logger = get_logger()


# Create a custom handler
custom_handler = CustomHandler()
custom_handler.setLevel(logging.INFO)
# Add the custom handler to the root logger
logging.getLogger().addHandler(custom_handler)
