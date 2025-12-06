'''
Created on Jun 22, 2021

@author: mballance
'''
import logging
import sys

# Create a module-level logger for ivpm
_logger = logging.getLogger("ivpm")


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


def note(msg):
    _logger.info(msg)


def warning(msg):
    _logger.warning(msg)

        
def error(msg):
    _logger.error(msg)

    
def fatal(msg):
    _logger.critical(msg)
    raise Exception(msg)
