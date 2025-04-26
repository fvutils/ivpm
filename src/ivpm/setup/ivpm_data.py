import os
import platform

Phase_SetupPre = "setup.pre"
Phase_SetupPost = "setup.post"
Phase_BuildPre = "build.pre"
Phase_BuildPost = "build.post"

_ivpm_extra_data = {}
_ivpm_extdep_data = []
_ivpm_hooks = {}
_ivpm_ext_name_m = {}
_package_dir = {}

def get_hooks(kind : str):
    global _ivpm_hooks
    if kind in _ivpm_hooks.keys():
        return _ivpm_hooks[kind]
    else:
        return []

def get_ivpm_extra_data():
    global _ivpm_extra_data
    return _ivpm_extra_data

def get_ivpm_extdep_data():
    global _ivpm_extdep_data
    return _ivpm_extdep_data

def get_ivpm_ext_name_m():
    global _ivpm_ext_name_m
    return _ivpm_ext_name_m

def get_package_dir():
    return _package_dir



def expand(subst_m, path):
    elems = path.split("/")

    # Perform path meta-variable substitution
    for i,e in enumerate(elems):
        found = True
        while found:
            found = False
            for k in subst_m.keys():
                if e.find(k) != -1:
                    found = True
                    e = e.replace(k, subst_m[k])
                    elems[i] = e
    return "/".join(elems)

def expand_libvars(src, libdir=None):
    libpref = "lib"
    dllext = ".so"
    if platform.system() == "Windows":
        libpref = ""
        dllext = ".dll"
    elif platform.system() == "Darwin":
        libpref = "lib"
        dllext = ".dylib"

    if libdir is None:
        libdir = "lib64" if os.path.isdir(os.path.join("build", "lib64")) else "lib"

    subst_m = {
        "{libdir}" : libdir,
        "{libpref}" : libpref,
        "{dllpref}" : libpref,
        "{dllext}" : dllext
    }

    return expand(subst_m, src)
