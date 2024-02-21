'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import yaml_srcinfo_loader
from yaml_srcinfo_loader.srcinfo import SrcInfo

import yaml

from ivpm.msg import fatal
from ivpm.package import Package, PackageType, SourceType, Ext2SourceType,\
    Spec2SourceType, Spec2PackageType
from ivpm.packages_info import PackagesInfo
from ivpm.proj_info import ProjInfo
from ivpm.utils import getlocstr


class IvpmYamlReader(object):
    
    def __init__(self):
        self.debug = False
        pass
    
    def read(self, fp, name) -> ProjInfo:
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
        
        if "deps" in pkg.keys() or "dev-deps" in pkg.keys():
            # old-style format
            print("Note: Package %s uses old-style ivpm.yaml format" % ret.name)
            if "deps" in pkg.keys() and pkg["deps"] is not None:
                ds = PackagesInfo("default")
                self.read_deps(ds, pkg["deps"])
                
                ret.set_dep_set("deps", ds)
                ret.set_dep_set("default", ds)
            if "dev-deps" in pkg.keys() and pkg["dev-deps"] is not None:
                ds = PackagesInfo("default-dev")
                self.read_deps(ds, pkg["dev-deps"])
                ret.set_dep_set("dev-deps", ds)
                ret.set_dep_set("default-dev", ds)
        elif "dep-sets" in pkg.keys():
            # new-style format
            self.read_dep_sets(ret, pkg["dep-sets"])
        else:
            # no dependencies at all
            print("Warning: no dependencies")
        
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
            
        return ret

    def read_dep_sets(self, info : ProjInfo, dep_sets):
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
            
            deps = ds_ent["deps"]
            
            if not isinstance(deps, list):
                raise Exception("deps is not a list")
            self.read_deps(ds, ds_ent["deps"])
            info.set_dep_set(ds.name, ds)
        

    def read_deps(self, ret : PackagesInfo, deps):
        
        for d in deps:
            si = d.srcinfo
            
            if "name" not in d.keys():
                raise Exception("Missing 'name' key at %s:%d:%d" % (
                    si.filename,
                    si.lineno,
                    si.linepos))
                
            url = d["url"] if "url" in d.keys() else None
#            if not "url" in d.keys():
#                raise Exception("Missing 'url' key at %s:%d:%d" % (
#                    si.filename,
#                    si.lineno,
#                    si.linepos))
                
            pkg = Package(d["name"], url)
            pkg.srcinfo = si
            
            if pkg.name in ret.keys():
                pkg1 = ret[pkg.name]
                fatal("Duplicate package %s @ %s ; previously speciifed @ %s" % (
                    pkg.name, getlocstr(pkg), getlocstr(pkg1)))
            else:
                ret.add_package(pkg)


            # Determine the source type (eg .git, .jar, etc)            
            if "src" in d.keys():
                if d["src"] not in Spec2SourceType.keys():
                    fatal("unsupported source type %s @ %s" % (d["src"], getlocstr(d["src"])))
                pkg.src_type = Spec2SourceType[d["src"]]
            else:
                if url is None:
                    fatal("no src specified for package %s and no URL specified" % pkg.name)

                ext = os.path.splitext(pkg.url)[1]
                
                if pkg.url.endswith(".tar.gz"):
                    ext = ".tar.gz"
                elif pkg.url.endswith(".tar.xz"):
                    ext = ".tar.xz"

                if not ext in Ext2SourceType.keys():
                    fatal("unknown URL extension %s" % ext)
                    
                pkg.src_type = Ext2SourceType[ext]
            if "dep-set" in d.keys():
                pkg.dep_set = d["dep-set"]
                
            # Determine the package type (eg Python, Raw)
            if "type" in d.keys():
                type_s = d["type"]
                if not type_s in Spec2PackageType.keys():
                    fatal("unknown package type %s @ %s ; Supported types 'raw', 'python'" % (
                        type_s, getlocstr(d["type"])))
                    
                pkg.pkg_type = Spec2PackageType[type_s]
            else:
                if url is None and pkg.src_type != SourceType.PyPi:
                    fatal("no type specified for package %s and no URL specified" % pkg.name)
                else:
                    # We'll need to auto-probe later once we have source
                    pkg.pkg_type = PackageType.Unknown

            if self.debug:                    
                print("pkg_type (%s): %s" % (pkg.url, str(pkg.pkg_type)))
            if pkg.src_type == SourceType.PyPi and (pkg.pkg_type is None or pkg.pkg_type == PackageType.Unknown):
                pkg.pkg_type = PackageType.Python
            
            if "version" in d.keys():
                pkg.version = d["version"]
                
            if "depth" in d.keys():
                pkg.depth = d["depth"]
                
            if "dep-set" in d.keys():
                pkg.dep_set = d["dep-set"]
                
            if "branch" in d.keys():
                pkg.branch = d["branch"]
                
            if "commit" in d.keys():
                pkg.commit = d["commit"]
                
            if "tag" in d.keys():
                pkg.tag = d["tag"]
                
            if "deps" in d.keys():
                if d["deps"] == "skip":
                    pkg.process_deps = False
                else:
                    fatal("Unknown value for 'deps': %s" % d["deps"])
                    
                
            if pkg.src_type is None:
                print("TODO: auto-detect source type")

        if self.debug:
            print("ret: %s %d packages" % (str(ret), len(ret.packages)))
        return ret

    def read_path_set(self, info : ProjInfo, path, ps_kind : str, ps):
        if ps_kind not in info.paths.keys():
            info.paths[ps_kind] = {}
        path_kind_s = info.paths[ps_kind]

        for p_kind in ps.keys():
            if p_kind not in path_kind_s.keys():
                path_kind_s[p_kind] = []
            for p in ps[p_kind]:
                path_kind_s[p_kind].append(os.path.join(path, p))

        
