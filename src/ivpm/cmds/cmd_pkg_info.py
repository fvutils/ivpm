#****************************************************************************
#* cmd_pkg_flags.py
#*
#* Copyright 2022 Matthew Ballance and Contributors
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

from ..pkg_info.pkg_info_rgy import PkgInfoRgy
from ..pkg_info.pkg_info_loader import PkgInfoLoader
from ..pkg_info.pkg_compile_flags import PkgCompileFlags

class CmdPkgInfo(object):

    def __init__(self):
        pass

    def __call__(self, args):
        rgy = PkgInfoRgy.inst()
        pkg_names = args.pkgs if isinstance(args.pkgs, list) else [args.pkgs]
        pkgs = []

        for pn in pkg_names:
            if not rgy.hasPkg(pn):
                raise Exception("Failed to find package %s" % pn)
            pkgs.append(rgy.getPkg(pn))

        if args.type == "flags":
            flags = PkgCompileFlags().flags(pkgs)
            print("%s" % " ".join(flags))
        elif args.type == "paths":
            paths = PkgCompileFlags().paths(pkgs, args.kind)
            print("%s" % " ".join(paths))
        elif args.type == "libdirs":
            paths = PkgCompileFlags().ldirs(pkgs, args.kind)
            print("%s" % " ".join(paths))
        elif args.type == "libs":
            paths = PkgCompileFlags().libs(pkgs, args.kind)
            print("%s" % " ".join(paths))
        else:
            raise Exception("Unimplemented pkg-info kind %s" % args.type)


