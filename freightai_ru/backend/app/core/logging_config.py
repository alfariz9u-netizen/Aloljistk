"""
Structured logging. Never logs secrets (tokens, phone numbers, API keys)
-- only IDs and action names. Every handler/endpoint should log through
this logger rather than print(), and real errors must be logged (never
`except Exception: pass`).
"""
import logging
import sys


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("freightai")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
        '"msg":%(message)r}'
    ))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = configure_logging()
