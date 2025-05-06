#****************************************************************************
#* package_handler_rgy.py
#*
#* Copyright 2023 Matthew Ballance and Contributors
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
#* Created on:
#*     Author: 
#*
#****************************************************************************
import dataclasses as dc
from .package_handler_list import PackageHandlerList
from .package_handler_python import PackageHandlerPython

class PackageHandlerRgy(object):

    _inst = None

    def __init__(self):
        self.handlers = []

    def addHandler(self, h):
        print("Handler: %s" % h.name)
        self.handlers.append(h)

    def _load(self):
        self.addHandler(PackageHandlerPython)

    def mkHandler(self):
        h = PackageHandlerList()
        for h_t in self.handlers:
            h.addHandler(h_t())
        return h

    @classmethod
    def inst(cls) -> 'PackageHandlerRgy':
        if cls._inst is None:
            cls._inst = PackageHandlerRgy()
            cls._inst._load()
        return cls._inst

