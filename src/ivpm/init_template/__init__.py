#****************************************************************************
#* __init__.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
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
"""Templates for ``ivpm init``.

The public API for template providers is :class:`InitTemplate`.  An
``ivpm.init_templates`` entry point returns paths (template or collection
directories) and/or ``InitTemplate`` instances::

    [project.entry-points."ivpm.init_templates"]
    acme = "acme_templates:get_templates"

This package deliberately imports neither copier nor the registry at load
time; only ``copier_runner`` imports copier.
"""
from .template_info import InitTemplate

__all__ = ["InitTemplate"]
