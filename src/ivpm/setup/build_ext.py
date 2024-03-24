#****************************************************************************
#* build_ext.py
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
import subprocess
import sys
from setuptools.command.build_ext import build_ext as _build_ext

class BuildExt(_build_ext):

    def build_extensions(self):
#        print("build_extesions %s" % os.getcwd())
#        build_py = self.get_finalized_command('install').root
#        print("build_py: %s" % str(build_py))
#        for f in dir(build_py):
#            print("Field: %s" % f)
#        print("Package dir: %s" % build_py.get_package_dir())
        proj_dir = os.getcwd()

        if os.path.isfile(os.path.join(proj_dir, "CMakeLists.txt")):
            print("build_cmake")
            self.build_cmake(proj_dir)
        
        super().build_extensions()

    def build_extension(self, ext):
        proj_dir = os.getcwd()
        print("build_extension: %s" % str(ext))
        include_dirs = getattr(ext, 'include_dirs', [])
#        include_dirs.append("foobar")
#        include_dirs.append(os.path.join(proj_dir, 'src', 'include'))
        setattr(ext, 'include_dirs', include_dirs)
        
        return super().build_extension(ext)
    
    def copy_extensions_to_source(self):
        import ivpm.setup.setup as ivpms
        from ivpm.setup.setup import get_hooks, Phase_BuildPre, Phase_BuildPost
        """ Like the base class method, but copy libs into proper directory in develop. """
        print("copy_extensions_to_source")
        for hook in get_hooks(Phase_BuildPre):
            hook(self)
        super().copy_extensions_to_source()

        # Appy any post-copy hooks
        for hook in get_hooks(Phase_BuildPost):
            hook(self)
        
        return

        build_py = self.get_finalized_command("build_py")
        
        ext = self.extensions[0]
        fullname = self.get_ext_fullname(ext.name)
        filename = self.get_ext_filename(fullname)
        modpath = fullname.split(".")
        package = ".".join(modpath[:-1])
        package_dir = build_py.get_package_dir(package)

        if sys.platform == "darwin":
            ext = ".dylib"
        elif sys.platform == "win32":
            ext = ".dll"
        else:
            ext = ".so"

        if sys.platform == "win32":
            pref = ""
        else:
            pref = "lib"

        copy_file(
            os.path.join(cwd, "build", "src", "%sdebug-mgr%s" % (pref, ext)),
            os.path.join(package_dir, "%sdebug-mgr%s" % (pref, ext)))
        
        if sys.platform == "win32":
            copy_file(
                os.path.join(cwd, "build", "src", "debug-mgr.lib" ),
                os.path.join(package_dir, "debug-mgr.lib" % (pref, ext)))

        dest_filename = os.path.join(package_dir, filename)
        
        print("package_dir: %s dest_filename: %s" % (package_dir, dest_filename))
    
    
    def build_cmake(self, proj_dir):
        build_cmd = {
            "Ninja": BuildExt.build_ninja,
            "Unix Makefiles": BuildExt.build_make
        }
        install_cmd = {
            'Ninja': BuildExt.install_ninja,
            'Unix Makefiles': BuildExt.install_make
        }

        DEBUG = False
        if "-DDEBUG" in sys.argv:
            DEBUG = True
        elif "DEBUG" in os.environ.keys() and os.environ["DEBUG"] in ("1", "y", "Y"):
            DEBUG = True
        
        if "CMAKE_BUILD_TOOL" in os.environ.keys():
            cmake_build_tool = os.environ["CMAKE_BUILD_TOOL"]
        else:
            cmake_build_tool = "Ninja"
            
        if cmake_build_tool not in build_cmd.keys():
            raise Exception("cmake_build_tool %s not supported" % cmake_build_tool)
            
        # First need to establish where things are

        if os.path.isdir(os.path.join(proj_dir, "packages")):
            print("Note: Packages are inside this directory")
            packages_dir = os.path.join(proj_dir, "packages")
        elif os.path.isdir(os.path.join(os.path.dirname(proj_dir), os.path.basename(proj_dir))):
            packages_dir = os.path.dirname(proj_dir)
        else:
            raise Exception("Unexpected source layout")

        # Now, build the core native library
        cwd = os.getcwd()
        build_dir = os.path.join(cwd, "build")
        if not os.path.isdir(build_dir):
            os.makedirs(build_dir)

        if DEBUG:
            BUILD_TYPE = "-DCMAKE_BUILD_TYPE=Debug"
        else:
            BUILD_TYPE = "-DCMAKE_BUILD_TYPE=Release"

        env = os.environ.copy()
        python_bindir = os.path.dirname(sys.executable)
        print("python_bindir: %s" % str(python_bindir))

        if "PATH" in env.keys():
            env["PATH"] = python_bindir + os.pathsep + env["PATH"]
        else:
            env["PATH"] = python_bindir

        # Isolate the cmake call from the environment PYTHONPATH
        if "PYTHONPATH" in env.keys():
            env.pop("PYTHONPATH")

        config_cmd = ["cmake", proj_dir, "-G%s" % cmake_build_tool, BUILD_TYPE]

        config_cmd.append("-DPACKAGES_DIR=%s" % packages_dir)
        config_cmd.append("-DCMAKE_INSTALL_PREFIX=%s" % os.path.join(cwd, "build"))

        if platform.system() == "Windows":
#            config_cmd.extend(["-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang"])
            pass
        elif platform.system() == "Darwin":
            config_cmd.append("-DCMAKE_OSX_ARCHITECTURES='x86_64;arm64'")

        print("cmake config command: %s" % str(config_cmd))
            
        # Run configure...
        result = subprocess.run(config_cmd, cwd=os.path.join(cwd, "build"), env=env)

        if result.returncode != 0:
            raise Exception("cmake configure failed")
            
        build_cmd[cmake_build_tool](self, build_dir, env)
        install_cmd[cmake_build_tool](self, build_dir, env)

        from ivpm.setup.setup import get_ivpm_extdep_data
        for src,dst in get_ivpm_extdep_data():
            shutil.copyfile(src, dst)

    def build_ninja(self, build_dir, env):
        cmd = ["ninja", "-j", "%d" % os.cpu_count() ]
        print("Command: %s" % str(cmd))
        try:
            result = subprocess.run(cmd, cwd=build_dir, env=env)
        except Exception as e:
            print("Exception: %s" % str(e))
        if result.returncode != 0:
            raise Exception("build failed")
    
    def build_make(self, build_dir, env):
        result = subprocess.run(
            ["make",
             "-j%d" % os.cpu_count()
            ],
            cwd=build_dir,
            env=env)
        if result.returncode != 0:
            raise Exception("build failed")
    
    def install_ninja(self, build_dir, env):
        result = subprocess.run(
            ["ninja",
             "-j",
             "%d" % os.cpu_count(),
             "install"
            ],
            cwd=build_dir,
            env=env)
        if result.returncode != 0:
            raise Exception("install failed")
    
    def install_make(self, build_dir, env):
        result = subprocess.run(
            ["make",
             "-j%d" % os.cpu_count(),
             "install"
            ],
            cwd=build_dir,
            env=env)
        if result.returncode != 0:
            raise Exception("install failed")
    
