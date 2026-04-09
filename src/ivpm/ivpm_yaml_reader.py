'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import yaml_srcinfo_loader
from typing import Dict, List
from yaml_srcinfo_loader.srcinfo import SrcInfo
from .package import Package
from .env_spec import EnvSpec

import yaml

from .utils import fatal, getlocstr, warning
from ivpm.package import Package, PackageType, SourceType
from ivpm.packages_info import PackagesInfo
from ivpm.pkg_content_type import parse_type_field


class IvpmYamlReader(object):
    
    def __init__(self):
        self.debug = False
        pass
    
    def read(self, fp, name) -> 'ProjInfo':
        from ivpm.proj_info import ProjInfo

        ret = ProjInfo(is_src=True)
        
        # File I/O streams have a name field that is read-only.
        # Add a name field to non-I/O streams
        if not hasattr(fp, "name"):
            fp.name = name

        data = yaml.load(fp, Loader=yaml_srcinfo_loader.Loader)
        
        if "package" not in data.keys():
            raise Exception("Missing 'package' section YAML file %s" % name)
        pkg = data["package"]
        
        if "name" not in pkg.keys():
            raise Exception("Missing 'name' key in YAML file %s" % name)
        
        
        ret.name = pkg["name"]
        
        if "version" in pkg.keys():
            ret.version = pkg["version"]
        else:
            ret.version = None

        if "type" in pkg.keys():
            ret.self_types = parse_type_field(pkg["type"])

        if "with" in pkg.keys():
            self._read_with_section(ret, pkg["with"], name)

        # Specify where sub-packages are stored. Defaults to 'packages'        
        if "deps-dir" in pkg.keys():
            ret.deps_dir = pkg["deps-dir"]

        if "default-dep-set" in pkg.keys():
            ret.default_dep_set = pkg["default-dep-set"]
        
        if "deps" in pkg.keys() or "dev-deps" in pkg.keys():
            # old-style format
            fatal("Package %s uses old-style ivpm.yaml format" % ret.name)
        elif "dep-sets" in pkg.keys():
            # new-style format
            self.read_dep_sets(ret, pkg["dep-sets"])
        else:
            # no dependencies at all
            warning("no dependencies")
        
        if "setup-deps" in pkg.keys():
            for sd in pkg["setup-deps"]:
                ret.setup_deps.add(sd)

        if "paths" in pkg.keys():
            ps = pkg["paths"]
            for ps_kind in ps.keys():
                self.read_path_set(
                    ret,
                    os.path.dirname(name),
                    ps_kind,
                    ps[ps_kind])
        
        if "env" in pkg.keys():
            es = pkg["env"]
            for evar in es:
                self.process_env_directive(
                    ret,
                    evar)
            
        return ret

    def _read_with_section(self, info: 'ProjInfo', with_data: dict, name: str):
        """Parse the package-level ``with:`` map into handler configurations."""
        from .proj_info import VenvMode, PythonConfig

        _KNOWN_WITH_KEYS = {"python"}

        for key in with_data.keys():
            if key not in _KNOWN_WITH_KEYS:
                # Store handler-specific config for later consumption
                # by registered handlers (e.g. cbwa, direnv).
                info.handler_configs[key] = with_data[key]

        if "python" in with_data.keys():
            py_data = with_data["python"]
            if py_data is None:
                py_data = {}
            cfg = PythonConfig()
            if "venv" in py_data:
                try:
                    cfg.venv = VenvMode.parse(py_data["venv"])
                except ValueError as e:
                    from .utils import fatal
                    fatal(str(e) + " in %s" % name)
            if "system-site-packages" in py_data:
                cfg.system_site_packages = bool(py_data["system-site-packages"])
            if "pre-release" in py_data:
                cfg.pre_release = bool(py_data["pre-release"])
            info.python_config = cfg

    def read_dep_sets(self, info : 'ProjInfo', dep_sets):
        if not isinstance(dep_sets, list):
            raise Exception("Expect body of dep-sets to be a list, not %s" % str(type(dep_sets)))
        
        for ds_ent in dep_sets:
            if not isinstance(ds_ent, dict):
                raise Exception("Dependency set is not a dict")
            if "name" not in ds_ent.keys():
                raise Exception("No name associated with dependency set")
            if "deps" not in ds_ent.keys():
                raise Exception("No 'deps' entry in dependency set")
            
            ds_name = ds_ent["name"]
            ds = PackagesInfo(ds_name)
            default_dep_set = None

            if "uses" in ds_ent.keys():
                ds.uses = str(ds_ent["uses"])

            if "default-dep-set" in ds_ent.keys():
                default_dep_set = ds_ent["default-dep-set"]

            deps = ds_ent["deps"]
            
            if not isinstance(deps, list):
                raise Exception("deps is not a list")
            self.read_deps(ds, ds_ent["deps"], default_dep_set)
            info.set_dep_set(ds.name, ds)

        self._resolve_dep_set_inheritance(info)

    def _resolve_dep_set_inheritance(self, info: 'ProjInfo'):
        """
        Merge inherited packages for every dep-set that declares a 'uses' base.
        The current dep-set's packages win on name collision.
        Detects cycles and unknown base names.
        """
        dep_set_m = info.dep_set_m
        resolved = set()

        def resolve(name, visiting):
            if name in resolved:
                return
            ds = dep_set_m[name]
            if ds.uses is None:
                resolved.add(name)
                return
            if name in visiting:
                cycle = " -> ".join(list(visiting) + [name])
                raise Exception(
                    "Cyclic dep-set inheritance detected: %s" % cycle)
            base_name = ds.uses
            if base_name not in dep_set_m:
                raise Exception(
                    "dep-set '%s' references unknown base dep-set '%s'"
                    % (name, base_name))
            visiting.add(name)
            resolve(base_name, visiting)
            visiting.discard(name)

            base_ds = dep_set_m[base_name]
            # Start with base packages, then let current overwrite
            merged_pkgs = base_ds.packages.copy()
            merged_pkgs.update(ds.packages)
            ds.packages = merged_pkgs

            merged_opts = base_ds.options.copy()
            merged_opts.update(ds.options)
            ds.options = merged_opts

            resolved.add(name)

        for ds_name in list(dep_set_m.keys()):
            resolve(ds_name, set())
        

    def read_deps(self, ret : PackagesInfo, deps, default_dep_set):
        from .pkg_types.pkg_type_rgy import PkgTypeRgy
        from .pkg_content_type_rgy import PkgContentTypeRgy
        
        for d in deps:
            si = d.srcinfo
            
            if "name" not in d.keys():
                raise Exception("Missing 'name' key at %s:%d:%d" % (
                    si.filename,
                    si.lineno,
                    si.linepos))

            if d["name"] in ret.keys():
                pkg1 = ret[d["name"]]
                fatal("Duplicate package %s @ %s ; previously speciifed @ %s" % (
                    d["name"], getlocstr(d), getlocstr(pkg1)))

            url = d["url"] if "url" in d.keys() else None

            # Determine the source of this package:
            # - Git
            # - http
            # ...

            src = "<unknown>"
            if "src" in d.keys():
                src = d["src"]
            elif "pypi" in d.keys() and d["pypi"]:
                src = "pypi"
            else:
                # Auto-probing the package based on the URL. The user can always
                # specify the source explicitly
                if url is None:
                    fatal("no src specified for package %s and no URL specified" % d["name"])

                if url.endswith(".git"):
                    src = "git"                
                elif url.startswith("http://") or url.startswith("https://"):
                    src = "http"
                elif url.startswith("file://"):
                    src = "file"
                else:
                    pt_rgy = PkgTypeRgy.inst()
                    raise Exception("Cannot determine source type from url %s. Please specify src as one of %s" % (
                        url, ", ".join(pt_rgy.getSrcTypes())))

            pt_rgy = PkgTypeRgy.inst()
            
            if not pt_rgy.hasPkgType(src):
                raise Exception("Package %s has unknown type %s" % (d["name"], src))
            pkg = PkgTypeRgy.inst().mkPackage(src, str(d["name"]), d, si)

            # Resolve content type from 'type:' field (string, dict, or list form).
            # 'with:' is no longer supported; options are now inline in the type dict.
            ct_rgy = PkgContentTypeRgy.inst()
            if "with" in d.keys():
                fatal("Package '%s': 'with:' is no longer supported; "
                      "use inline options instead, e.g. type: { python: { editable: false } } @ %s" % (
                          pkg.name, getlocstr(d["with"])))
            if "type" in d.keys():
                raw = parse_type_field(d["type"])
                for type_name, opts in raw:
                    if not ct_rgy.has(type_name):
                        fatal("Package '%s': unknown type '%s' @ %s ; known types: %s" % (
                            pkg.name, type_name, getlocstr(d["type"]),
                            ", ".join(ct_rgy.names())))
                    pkg.type_data.append(ct_rgy.get(type_name).create_data(opts, si))

            # Unless specified, load the same dep-set from sub-packages
            if pkg.dep_set is None:
                if default_dep_set is not None:
                    pkg.dep_set = default_dep_set
                else:
                    pkg.dep_set = ret.name

#            print("Using dep-set %s for package %s" % (pkg.dep_set, pkg.name))

            ret.add_package(pkg)

            if self.debug:                    
                print("pkg_type (%s): %s" % (pkg.url, str(pkg.pkg_type)))

        if self.debug:
            print("ret: %s %d packages" % (str(ret), len(ret.packages)))
        return ret

    def read_path_set(self, info : 'ProjInfo', path, ps_kind : str, ps):
        if ps_kind not in info.paths.keys():
            info.paths[ps_kind] = {}
        path_kind_s = info.paths[ps_kind]

        for p_kind in ps.keys():
            if p_kind not in path_kind_s.keys():
                path_kind_s[p_kind] = []
            for p in ps[p_kind]:
                path_kind_s[p_kind].append(os.path.join(path, p))

    def process_env_directive(self,
                              info : 'ProjInfo',
                              evar : Dict):
        if "name" not in evar.keys():
            raise Exception("No variable-name specified: %s" % str(evar))
        act = None
        act_s = None
        val = None
        for an,av in [
            ("value", EnvSpec.Act.Set),
            ("path", EnvSpec.Act.Path),
            ("path-append", EnvSpec.Act.PathAppend),
            ("path-prepend", EnvSpec.Act.PathPrepend)]:
            if an in evar.keys():
                if act is not None:
                    raise Exception("Multiple variable-setting directives specified: %s and %s" % (
                        act_s, an))
                act_s = an
                act = av
                val = evar[an]
            
        if act is None:
            raise Exception(
                "No variable-directive setting (value, path, path-append, path-prepend) specified")
        info.env_settings.append(EnvSpec(evar["name"], val, act))



