'''
Created on Jun 22, 2021

@author: mballance
'''
import logging
import sys

from .diagnostics import (
    DiagnosticReporter, PlainSink, RichSink, Severity, SrcLoaderError,
)

# Developer logging channel (debug/verbose traces). User-facing diagnostics no
# longer ride on this -- they go through the reporter below and are always shown.
_logger = logging.getLogger("ivpm")

# Process-wide diagnostic reporter. Default sink writes to stderr unconditionally
# so warnings/errors are never suppressed by the logging level. A TUI may swap in
# a RichSink via use_sink().
_reporter = DiagnosticReporter()


def setup_logging(log_level: str = "NONE"):
    """
    Configure the logging module based on the specified log level.

    Args:
        log_level: One of "NONE", "INFO", "DEBUG", "WARN"
    """
    level_map = {
        "NONE": logging.CRITICAL + 1,  # Effectively disable logging
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "WARN": logging.WARNING,
    }

    level = level_map.get(log_level.upper(), logging.CRITICAL + 1)

    # Configure root logger for ivpm
    logger = logging.getLogger("ivpm")
    logger.setLevel(level)

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    if log_level.upper() != "NONE":
        # Add a console handler with appropriate formatting
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        formatter = logging.Formatter('%(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)


# --------------------------------------------------------------------------
# Reporter access / sink management
# --------------------------------------------------------------------------

def get_reporter() -> DiagnosticReporter:
    return _reporter


def set_reporter(reporter: DiagnosticReporter) -> DiagnosticReporter:
    """Replace the process-wide reporter (mainly for tests). Returns the old."""
    global _reporter
    prev = _reporter
    _reporter = reporter
    return prev


def use_sink(sink) -> object:
    """Install *sink* on the current reporter; return the previous sink.

    TUIs call this on start (with a RichSink) and restore on stop.
    """
    prev = _reporter.sink
    _reporter.sink = sink
    return prev


# --------------------------------------------------------------------------
# Diagnostic emitters (back-compatible: msg may be a plain string; loc optional)
# --------------------------------------------------------------------------

def note(msg, loc=None):
    _reporter.note(msg, loc)


def warning(msg, loc=None):
    _reporter.warning(msg, loc)


def error(msg, loc=None):
    # Records and renders; does NOT raise (accumulating).
    _reporter.error(msg, loc)


def fatal(msg, loc=None):
    # Renders and raises SrcLoaderError.
    _reporter.fatal(msg, loc)
