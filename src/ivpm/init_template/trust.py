#****************************************************************************
#* trust.py
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
"""Whether a template may use copier's *unsafe* features.

copier refuses ``_tasks``, ``_migrations`` and ``_jinja_extensions`` unless
told the template is trusted.  The registry has already resolved each
template's ``trusted`` flag from where it came from (§6 of the design):

==========================  ==========================
origin                      trusted by default
==========================  ==========================
built-in                    yes
entry point / SiteConfig    yes
config-file entry           only with ``trust: true``
IVPM_INIT_TEMPLATE_PATH     no
path or URL on the CLI      no
==========================  ==========================

``--trust`` overrides it for one invocation.  copier's own ``trust:``
settings list is still honored by copier when IVPM says "not trusted".
"""
from .template_info import InitTemplate


def is_trusted(template: InitTemplate, cli_trust: bool = False) -> bool:
    return bool(cli_trust) or bool(template.trusted)


def trust_hint(template: InitTemplate) -> str:
    """How the user can trust *template*, for the 'unsafe template' error."""
    if template.origin in ("user-config", "site-config-file"):
        return ("If you trust it, pass --trust, or add 'trust: true' to its "
                "init-templates entry in your IVPM config file.")
    return "If you trust it, pass --trust."
