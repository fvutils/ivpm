#****************************************************************************
#* loader.py
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
In-tree YAML loader that annotates parsed values with source-location info.

``SrcInfoLoader`` subclasses ``yaml.SafeLoader`` and attaches a ``.srcinfo``
attribute (a :class:`SrcInfo`) to every attributable value -- mappings,
sequences, and ``str``/``int``/``float`` scalars -- built from the exact
``node.start_mark``/``node.end_mark`` PyYAML provides. ``bool``/``null`` values
are the interned singletons and cannot carry an attribute; their location is
obtained from the enclosing container.

This replaces the external ``pyyaml-srcinfo-loader`` dependency while preserving
the ``.srcinfo`` attribute contract that IVPM relies on.
"""
import yaml

from .srcinfo import SrcInfo, SrcText


# Attribute-carrying subclasses of the built-in types. No __slots__: subclasses
# of the variable-length built-ins (int/str) reject non-empty __slots__, and the
# per-instance __dict__ is exactly what lets us pin ``.srcinfo``. Config files
# are tiny, so the overhead is irrelevant.
class _Dict(dict):
    pass


class _List(list):
    pass


class _Str(str):
    pass


class _Int(int):
    pass


class _Float(float):
    pass


class SrcLoaderError(Exception):
    """Raised for YAML problems that carry a source location.

    Also the exception type raised by the diagnostics layer's ``fatal()`` and
    ``abort_if_errors()``. ``diagnostics`` is a list of opaque diagnostic
    records (typed in ``ivpm.diagnostics``); kept untyped here to avoid a
    circular import.
    """

    def __init__(self, message, diagnostics=None, srcinfo=None):
        super().__init__(message)
        self.message = message
        self.diagnostics = list(diagnostics) if diagnostics else []
        self.srcinfo = srcinfo

    @classmethod
    def from_marked(cls, e, srctext=None):
        """Build a SrcLoaderError from a ``yaml.MarkedYAMLError``."""
        mark = getattr(e, "problem_mark", None)
        if mark is None:
            mark = getattr(e, "context_mark", None)
        si = None
        if mark is not None:
            filename = srctext.filename if srctext is not None \
                else getattr(mark, "name", "<unknown>")
            si = SrcInfo(filename,
                         mark.line + 1, mark.column + 1,
                         srctext=srctext)
        problem = getattr(e, "problem", None)
        if problem is None:
            problem = str(e)
        msg = "%s: %s" % (si, problem) if si is not None else problem
        return cls(msg, srcinfo=si)


class SrcInfoLoader(yaml.SafeLoader):
    """A SafeLoader that records ``.srcinfo`` spans on parsed values."""

    def __init__(self, stream):
        # Capture the file name and full text up front so we can (a) report the
        # correct filename in marks and (b) render source excerpts later. We
        # then hand the text -- not the original stream -- to the base Reader.
        name = getattr(stream, "name", None)
        if hasattr(stream, "read"):
            text = stream.read()
        else:
            text = stream
        self._srctext = SrcText(name, text)
        super().__init__(text)

    def _si(self, node):
        sm = node.start_mark
        em = node.end_mark
        return SrcInfo(
            self._srctext.filename,
            sm.line + 1, sm.column + 1,
            em.line + 1, em.column + 1,
            srctext=self._srctext)

    # -- containers (generators, mirroring SafeConstructor) ------------------

    def construct_yaml_map(self, node):
        data = _Dict()
        data.srcinfo = self._si(node)
        yield data
        value = self.construct_mapping(node)
        data.update(value)

    def construct_yaml_seq(self, node):
        data = _List()
        data.srcinfo = self._si(node)
        yield data
        data.extend(self.construct_sequence(node))

    # -- attributable scalars ------------------------------------------------

    def construct_yaml_str(self, node):
        value = super().construct_yaml_str(node)
        ret = _Str(value)
        ret.srcinfo = self._si(node)
        return ret

    def construct_yaml_int(self, node):
        value = super().construct_yaml_int(node)
        ret = _Int(value)
        ret.srcinfo = self._si(node)
        return ret

    def construct_yaml_float(self, node):
        value = super().construct_yaml_float(node)
        ret = _Float(value)
        ret.srcinfo = self._si(node)
        return ret

    # bool / null: left to the stock SafeLoader constructors (interned
    # singletons -- cannot carry an attribute). Their location is available
    # from the enclosing container's ``.srcinfo``.


# Register the overrides on the subclass only (add_constructor copies the
# inherited table into the subclass on first call, so SafeLoader is untouched).
SrcInfoLoader.add_constructor(
    'tag:yaml.org,2002:map', SrcInfoLoader.construct_yaml_map)
SrcInfoLoader.add_constructor(
    'tag:yaml.org,2002:seq', SrcInfoLoader.construct_yaml_seq)
SrcInfoLoader.add_constructor(
    'tag:yaml.org,2002:str', SrcInfoLoader.construct_yaml_str)
SrcInfoLoader.add_constructor(
    'tag:yaml.org,2002:int', SrcInfoLoader.construct_yaml_int)
SrcInfoLoader.add_constructor(
    'tag:yaml.org,2002:float', SrcInfoLoader.construct_yaml_float)

# Drop-in alias for code that used ``yaml.load(fp, Loader=...)``.
Loader = SrcInfoLoader


def load(stream, name=None):
    """Parse a single YAML document, annotating values with ``.srcinfo``.

    Returns the parsed data. Raises :class:`SrcLoaderError` (wrapping a
    ``yaml.MarkedYAMLError``) with a populated :class:`SrcInfo` on syntax
    errors, so callers never see a raw PyYAML traceback.
    """
    if name is not None and not getattr(stream, "name", None):
        try:
            stream.name = name
        except (AttributeError, TypeError):
            pass
    loader = SrcInfoLoader(stream)
    try:
        return loader.get_single_data()
    except yaml.MarkedYAMLError as e:
        raise SrcLoaderError.from_marked(e, loader._srctext) from e
    finally:
        loader.dispose()
