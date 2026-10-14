#****************************************************************************
#* errors.py
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
from typing import Optional


class InitError(Exception):
    """An expected ``ivpm init`` failure: printed without a traceback.

    *hint* is an optional second line that tells the user what to do next.
    """

    def __init__(self, msg: str, hint: Optional[str] = None):
        super().__init__(msg)
        self.msg = msg
        self.hint = hint

    def __str__(self):
        if self.hint:
            return "%s\n  %s" % (self.msg, self.hint)
        return self.msg
