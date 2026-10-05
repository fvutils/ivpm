#****************************************************************************
#* internal_error.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may
#* not use this file except in compliance with the License.
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software
#* distributed under the License is distributed on an "AS IS" BASIS,
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#* See the License for the specific language governing permissions and
#* limitations under the License.
#*
#****************************************************************************
"""Reporting of unexpected exceptions -- bugs in IVPM or in a handler.

IVPM's own error types (``SrcLoaderError`` from ``fatal()``,
``HandlerFatalError``) describe conditions the user can act on, and the
dispatch sites render them as such. Anything else -- an ``AttributeError``, a
``KeyError``, an ``OSError`` nobody anticipated -- is a bug. The user should
get one line saying what was running when it happened and where the stack
trace went, not the stack trace itself, and a distinct exit status so a script
can tell "your ivpm.yaml is wrong" from "ivpm is broken".

The conversion happens as close to the raise as the handler is still known
(``call_handler``, used by ``PackageHandlerList``), again at the dispatch
sites, and finally in ``main()`` for everything else.
"""
import logging
import os
import tempfile
import time
import traceback
from typing import Optional

_logger = logging.getLogger("ivpm")

#: Exit status for a user-facing error: bad input, a failed fetch, a handler
#: reporting an expected failure.
EXIT_USER_ERROR = 1

#: Exit status for an internal error (sysexits.h EX_SOFTWARE). Distinct from
#: EXIT_USER_ERROR and from argparse's usage status (2).
EXIT_INTERNAL_ERROR = 70


class InternalError(Exception):
    """An unexpected exception, wrapped with what IVPM was doing at the time.

    The stack trace of *cause* is written to a file when the error is created,
    because that is the last point it is guaranteed to be intact (an asyncio
    gather, for one, hands exceptions back detached from their context).
    """

    def __init__(self, cause: BaseException, action: str,
                 handler: Optional[str] = None):
        self.cause = cause
        self.action = action
        self.handler = handler
        self.trace_path = _write_trace(cause, self._context())
        super().__init__(self.summary())

    def _context(self) -> str:
        if self.handler:
            return "internal error in handler '%s' while %s" % (
                self.handler, self.action)
        return "internal error while %s" % self.action

    def summary(self) -> str:
        """The one line the user sees."""
        text = str(self.cause).strip()
        what = "%s: %s" % (type(self.cause).__name__, text) if text \
            else type(self.cause).__name__
        if self.trace_path:
            where = "stack trace written to %s" % self.trace_path
        else:
            where = "run with --log-level DEBUG for the stack trace"
        return "%s: %s (%s)" % (self._context(), what, where)


def _write_trace(exc: BaseException, context: str) -> Optional[str]:
    """Write *exc*'s stack trace to a fresh file and return its path.

    Also sent to the debug log, so ``--log-level DEBUG`` shows it inline.
    Returns None if no file could be written; the caller then points the user
    at the debug log instead.
    """
    text = "".join(traceback.format_exception(type(exc), exc,
                                              exc.__traceback__))
    _logger.debug("%s\n%s", context, text)
    try:
        fd, path = tempfile.mkstemp(
            prefix="ivpm-internal-error-%s-" % time.strftime("%Y%m%d-%H%M%S"),
            suffix=".log")
        with os.fdopen(fd, "w") as fp:
            fp.write("%s\n\n%s" % (context, text))
        return path
    except OSError:
        return None


def is_expected(exc: BaseException) -> bool:
    """True if *exc* is one of IVPM's own error reports, not a bug."""
    from .msg import SrcLoaderError
    from .handlers.package_handler import HandlerFatalError
    return isinstance(exc, (SrcLoaderError, HandlerFatalError, InternalError))


def handler_name(handler) -> Optional[str]:
    if handler is None:
        return None
    return getattr(handler, "name", None) or type(handler).__name__


def call_handler(handler, action: str, fn, *args):
    """Call ``fn(*args)``, converting an unexpected exception to InternalError.

    IVPM's own errors pass through untouched; the dispatch sites already know
    how to report those.
    """
    try:
        return fn(*args)
    except Exception as e:
        if is_expected(e):
            raise
        raise InternalError(e, action, handler_name(handler)) from e
