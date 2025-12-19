#****************************************************************************
#* install_lib.py
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
import os
import platform
import shutil
from setuptools.command.install_lib import install_lib as _install_lib

class InstallLib(_install_lib):
    
    def install(self):
        from ivpm import setup as ivpms
        from ivpm.setup.ivpm_data import get_ivpm_extra_data, get_ivpm_ext_name_m, expand_libvars
        # Assume 
        # May need to install some additional libraries and data
        # - data and/or include files to package 'share'
        #   - Map should be organized by dest package
        #   - Must be able to specify source relative to project root
        # - libraries to be included with packages
        #   - Map should be organized by dest package
        #   - 

        ivpm_extra_data = get_ivpm_extra_data()
        print("ivpm_extra_data=%s" % str(ivpm_extra_data))

        install_cmd = self.get_finalized_command('install')
        install_root = self.get_finalized_command('install').root

        if install_root is None:
            return



        build_py = self.get_finalized_command('build_py')
        ext_name_m = get_ivpm_ext_name_m()
        for p in build_py.packages:
            if p in ivpm_extra_data.keys():
                for spec in ivpm_extra_data[p]:
                    src = expand_libvars(spec[0])
                    if not os.path.isabs(src):
                      src = os.path.join(os.getcwd(), src)
                    if not os.path.isfile(src) and not os.path.isdir(src):
                        for libdir in ["lib", "lib64"]:
                            src_t = expand_libvars(spec[0], libdir=libdir)
                            print("Try src_t: %s" % src_t)
                            if os.path.isfile(src_t) or os.path.isdir(src_t):
                                print("... Found")
                                src = src_t
                                break
                    print("src: %s" % src)
                    dst = spec[1]

                    if not os.path.isfile(src) and not os.path.isdir(src):
                        for libdir in ["lib", "lib64"]:
                            src_t = expand_libvars(spec[0], libdir=libdir)
                            if os.path.isfile(src_t) or os.path.isdir(src_t):
                                src = src_t
                                break

                    if os.path.isfile(src):
                        dst_file = os.path.join(install_root, p, dst, os.path.basename(src))
                        dst_dir = os.path.dirname(dst_file)
                        if not os.path.isdir(dst_dir):
                            os.makedirs(dst_dir)
                        shutil.copyfile(src, dst_file)

                        if "{dllext}" in spec[0] and platform.system() == "Windows":
                            # See if there is a link library to copy as well
                            link_lib = src.replace(".dll", ".lib")
                            print("Test link_lib: %s" % link_lib)
                            if os.path.isfile(link_lib):
                                print("Found")
                                shutil.copyfile(
                                    link_lib,
                                    os.path.join(install_root, p, dst, os.path.basename(link_lib))
                                )
                            else:
                                print("Not Found")
                    elif os.path.isdir(src):
#                        if not os.path.isdir(os.path.join(install_root, p, spec[1])):
#                            os.makedirs(os.path.join(install_root, p, spec[1]), exist_ok=True)
#                        if os.path.isdir(os.path.join(install_root, p, dst)):
#                            print("rmtree: %s" % os.path.join(install_root, p, dst))
#                            shutil.rmtree(os.path.join(install_root, p, dst))

                        dst_dir = os.path.join(install_root, p, dst, os.path.basename(src))
                        if not os.path.isdir(dst_dir):
                            os.makedirs(dst_dir, exist_ok=True)

                        shutil.copytree(
                            src, 
                            dst_dir,
                            dirs_exist_ok=True)
                    else:
                        raise Exception("Source path \"%s\" doesn't exist" % src)
                    print("Copy: %s" % str(spec))

        # print("install_lib::install")
        # build_cmd = self.get_finalized_command('build_ext')
        # print("build_cmd: %s" % str(build_cmd))
        # print("install_root: %s" % install_root)
        # print("build_dir: %s" % self.build_dir)
        # for e in dir(self):
        #     if not e.startswith("_"):
        #         print("field: %s = %s" % (e, str(getattr(self, e))))
        # for e in dir(build_cmd):
        #     if not e.startswith("_"):
        #         print("bfield: %s = %s" % (e, str(getattr(build_cmd, e))))
        # for e in dir(install_cmd):
        #     if not e.startswith("_"):
        #         print("ifield: %s = %s" % (e, str(getattr(install_cmd, e))))
        
#         for ext in build_cmd.extensions:
#             incdirs = getattr(ext, "include_dirs", None)
#             print("Ext: %s" % ext.name)
#             if len(incdirs) > 0:
#                 pkg_name = ext.name.split('.')[0]
#                 dst = os.path.join(install_root, pkg_name, "share")
#                 print("dst: %s" % dst)
# #                if not os.path.isdir(dst):
# #                    os.makedirs(dst)
#                 dst = os.path.join(dst, "include")
#                 shutil.copytree(
#                     incdirs[0],
#                     dst
#                 )
#                 pass

        print("--> super.install")
        ret = super().install()
        print("<-- super.install")
        return ret
    



