#****************************************************************************
#* diagnostics.py
#*
#* Copyright 2024 Matthew Ballance and Contributors
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
"""
Structured, source-aware diagnostics for IVPM.

A single :class:`DiagnosticReporter` is the choke point for user-facing notes,
warnings, and errors. It renders through a pluggable :class:`DiagnosticSink`:

* :class:`PlainSink` (default) writes to ``stderr`` **unconditionally** -- so
  warnings/errors are never silenced by the logging level, unlike the historical
  ``msg`` helpers.
* :class:`RichSink` renders through a ``rich`` console; the ``update``/``sync``
  TUIs install one bound to their live console so diagnostics compose with the
  Live display instead of corrupting it.

``error()`` accumulates (does not raise) so problems found deep in processing can
all be reported in one run; ``abort_if_errors()`` stops at a phase boundary with
a summary. ``fatal()`` renders and raises immediately.

``SrcLoaderError`` (the exception type) lives in :mod:`ivpm.yamlsrc` and is
re-exported here.
"""
import enum
import sys

from .yamlsrc import SrcInfo, SrcLoaderError

__all__ = [
    "Severity",
    "Diagnostic",
    "DiagnosticSink",
    "PlainSink",
    "RichSink",
    "DiagnosticReporter",
    "SrcLoaderError",
]


class Severity(enum.IntEnum):
    NOTE = 0
    WARNING = 1
    ERROR = 2
    FATAL = 3


def _resolve_si(loc):
    """Coerce *loc* (a SrcInfo, or any object carrying a ``.srcinfo``) to a
    SrcInfo, or return None."""
    if loc is None:
        return None
    # A SrcInfo-like object (duck-typed: has the location fields directly).
    if hasattr(loc, "lineno") and hasattr(loc, "filename"):
        return loc
    return getattr(loc, "srcinfo", None)


def _has_loc(si):
    return si is not None and getattr(si, "filename", None) is not None \
        and getattr(si, "lineno", -1) >= 0


class Diagnostic(object):
    """A single diagnostic: severity + message + optional source location."""

    def __init__(self, severity, message, loc=None, notes=()):
        self.severity = severity
        self.message = message
        self.srcinfo = _resolve_si(loc)
        self.notes = list(notes)

    def loc_message(self):
        """The message prefixed with ``file:line:col`` when known (no severity).

        Used as the raised-exception text so callers that match on the original
        message still work when no location is present.
        """
        if _has_loc(self.srcinfo):
            return "%s: %s" % (self.srcinfo, self.message)
        return self.message

    def format(self, excerpt=False):
        """Render ``file:line:col: severity: message`` (+ optional excerpt and
        ``note:`` sub-lines)."""
        loc = ("%s: " % self.srcinfo) if _has_loc(self.srcinfo) else ""
        out = "%s%s: %s" % (loc, self.severity.name.lower(), self.message)
        if excerpt and self.srcinfo is not None:
            ex = None
            try:
                ex = self.srcinfo.excerpt()
            except Exception:
                ex = None
            if ex:
                out += "\n" + ex
        for n in self.notes:
            out += "\n" + n.format(excerpt=False)
        return out


# --------------------------------------------------------------------------
# Sinks
# --------------------------------------------------------------------------

class DiagnosticSink(object):
    """Renders a Diagnostic somewhere. Override :meth:`emit`."""

    def emit(self, diag):
        raise NotImplementedError()


def _want_excerpt(diag):
    return diag.severity >= Severity.ERROR


class PlainSink(DiagnosticSink):
    """Writes plain text to a stream (``stderr`` by default), always."""

    def __init__(self, out=None):
        self.out = out

    def emit(self, diag):
        out = self.out if self.out is not None else sys.stderr
        print(diag.format(excerpt=_want_excerpt(diag)), file=out)


class RichSink(DiagnosticSink):
    """Renders through a ``rich`` console (severity-colored).

    Bound to a TUI's console, this prints above an active Live region rather
    than corrupting it.
    """

    _STYLE = {
        Severity.NOTE: "cyan",
        Severity.WARNING: "yellow",
        Severity.ERROR: "red",
        Severity.FATAL: "bold red",
    }

    def __init__(self, console):
        self.console = console

    def emit(self, diag):
        from rich.text import Text
        text = diag.format(excerpt=_want_excerpt(diag))
        # Text(...) avoids interpreting any '['/']' in the message as markup.
        self.console.print(Text(text), style=self._STYLE.get(diag.severity))


class CollectingSink(DiagnosticSink):
    """Test helper: records emitted Diagnostics instead of printing."""

    def __init__(self):
        self.records = []

    def emit(self, diag):
        self.records.append(diag)

    def messages(self):
        return [d.format(excerpt=False) for d in self.records]


# --------------------------------------------------------------------------
# Reporter
# --------------------------------------------------------------------------

class DiagnosticReporter(object):
    """Routes diagnostics to a sink, counts them, and supports accumulation."""

    def __init__(self, sink=None):
        self.sink = sink if sink is not None else PlainSink()
        self.errors = []
        self.warning_count = 0

    def emit(self, diag):
        self.sink.emit(diag)
        if diag.severity is Severity.WARNING:
            self.warning_count += 1
        elif diag.severity >= Severity.ERROR:
            self.errors.append(diag)
        if diag.severity is Severity.FATAL:
            raise SrcLoaderError(diag.loc_message(), [diag], srcinfo=diag.srcinfo)

    # -- convenience emitters --------------------------------------------
    def note(self, message, loc=None):
        self.emit(Diagnostic(Severity.NOTE, message, loc))

    def warning(self, message, loc=None):
        self.emit(Diagnostic(Severity.WARNING, message, loc))

    def error(self, message, loc=None):
        # records + renders; does NOT raise (accumulating)
        self.emit(Diagnostic(Severity.ERROR, message, loc))

    def fatal(self, message, loc=None):
        # renders + raises SrcLoaderError
        self.emit(Diagnostic(Severity.FATAL, message, loc))

    # -- accumulation ----------------------------------------------------
    @property
    def error_count(self):
        return len(self.errors)

    def summary(self):
        return "%d error(s), %d warning(s)" % (self.error_count, self.warning_count)

    def reset(self):
        self.errors = []
        self.warning_count = 0

    def abort_if_errors(self):
        """Raise SrcLoaderError carrying all accumulated errors, if any."""
        if self.errors:
            raise SrcLoaderError(self.summary(), list(self.errors))
