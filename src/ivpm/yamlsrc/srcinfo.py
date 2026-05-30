#****************************************************************************
#* srcinfo.py
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
Source-location data carriers for the in-tree YAML loader.

``SrcInfo`` preserves the field/``__str__`` surface of the historical
``yaml_srcinfo_loader.srcinfo.SrcInfo`` (``filename``/``lineno``/``linepos``)
so existing consumers (``utils.getlocstr``, ``Package.srcinfo``, etc.) keep
working unchanged, while adding an end position (a full span) and an optional
reference to the source text so diagnostics can render an excerpt + caret.
"""


class SrcText(object):
    """Holds the text of a source file so excerpts/carets can be rendered.

    The lines are split lazily on first use. ``text`` may be ``None`` (e.g. the
    source was not retained), in which case ``line_text`` returns ``None``.
    """

    def __init__(self, filename=None, text=None):
        self.filename = filename if filename is not None else "<unknown>"
        self._text = text
        self._lines = None

    def line_text(self, lineno):
        """Return the text of the 1-based ``lineno`` (without newline), or None."""
        if self._text is None:
            return None
        if self._lines is None:
            self._lines = self._text.splitlines()
        if 1 <= lineno <= len(self._lines):
            return self._lines[lineno - 1]
        return None


class SrcInfo(object):
    """A 1-based source location (span) for a parsed YAML value.

    ``lineno``/``linepos`` are the start; ``end_lineno``/``end_linepos`` the
    end (exclusive, as reported by PyYAML's ``end_mark``). A value of ``-1``
    means "unknown" (matching the historical default).
    """

    def __init__(self,
                 filename=None,
                 lineno=-1,
                 linepos=-1,
                 end_lineno=-1,
                 end_linepos=-1,
                 srctext=None):
        # --- historical surface (do not change) ---
        self.filename = filename
        self.lineno   = lineno
        self.linepos  = linepos
        # --- span end ---
        self.end_lineno  = end_lineno
        self.end_linepos = end_linepos
        # --- source text for excerpt rendering (optional) ---
        self._srctext = srctext

    @property
    def has_loc(self):
        return self.lineno >= 0

    def excerpt(self):
        """Return ``<source-line>\\n<caret>`` for the start position, or None.

        Returns None when no source text is available or the location is
        unknown.
        """
        if self._srctext is None or self.lineno < 1 or self.linepos < 1:
            return None
        line = self._srctext.line_text(self.lineno)
        if line is None:
            return None
        caret = (" " * (self.linepos - 1)) + "^"
        return "%s\n%s" % (line, caret)

    def __str__(self):
        if self.filename is not None:
            return "%s:%d:%d" % (self.filename, self.lineno, self.linepos)
        else:
            return "%d:%d" % (self.lineno, self.linepos)

    def __repr__(self):
        return "SrcInfo(%s)" % str(self)
