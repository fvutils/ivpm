'''
Created on Jun 8, 2021

@author: mballance
'''
import os

import requirements

from ivpm import msg
from ivpm.ivpm_yaml_reader import IvpmYamlReader
from ivpm.ivpm_yaml_writer import IvpmYamlWriter
from ivpm.msg import fatal
from ivpm.out_wrapper import OutWrapper
from ivpm.package import Package, SourceType, PackageType
from ivpm.packages_info import PackagesInfo
from ivpm.packages_mf_reader import PackagesMfReader
from ivpm.proj_info import ProjInfo


class ProjectInfoReader(object):
    
    def __init__(self, proj_dir):   
        self.proj_dir = proj_dir
        pass
    
    def read(self):
        return self._read()
        pass
    
    def _read(self) -> ProjInfo:
        ret : ProjInfo = None
        
        # First, see if this is a new-style project
        if os.path.isfile(os.path.join(self.proj_dir, "ivpm.yaml")):
            msg.note("Reading ivpm.yaml")
            path = os.path.join(self.proj_dir, "ivpm.yaml");
            with open(path, "r") as fp:
                ret = IvpmYamlReader().read(fp, path)
        else:
            ret = ProjInfo(False)
            ret.name = os.path.basename(self.proj_dir)
            
            if os.path.isfile(os.path.join(self.proj_dir, "etc", "ivpm.info")):
                msg.warning("Reading legacy project (etc/ivpm.info)")
                etc_dir = os.path.join(self.proj_dir, "etc")
                packages_dir = os.path.join(self.proj_dir, "packages")
                
                if os.path.isfile(os.path.join(self.proj_dir, "etc", "packages.mf")):
                    with open(os.path.join(self.proj_dir, "etc", "packages.mf"), "r") as fp:
                        deps = PackagesMfReader().read(fp,
                            os.path.join(self.proj_dir, "etc", "packages.mf"))
                else:
                    deps = PackagesInfo()

                # Give both the same paths                    
                ret.deps = deps.copy()
                ret.dev_deps = deps.copy()

                if os.path.isfile(os.path.join(etc_dir, "ivpm.info")):
                    info = self.read_info(os.path.join(etc_dir, "ivpm.info"))
                    ret.name = info["name"]
                else:
                    info = None
                    
                if os.path.isfile(os.path.join(self.proj_dir, "requirements.txt")):
                    self.process_requirements(ret.deps, 
                                    os.path.join(self.proj_dir, "requirements.txt"))
                if os.path.isfile(os.path.join(self.proj_dir, "requirements_dev.txt")):
                    self.process_requirements(ret.dev_deps, 
                                    os.path.join(self.proj_dir, "requirements_dev.txt"))
                
            elif os.path.isfile(os.path.join(self.proj_dir, "requirements.txt")):
                msg.warning("Reading legacy requirements.txt-based project")
                if os.path.isfile(os.path.join(self.proj_dir, "requirements.txt")):
                    self.process_requirements(ret.deps, 
                                    os.path.join(self.proj_dir, "requirements.txt"))
                if os.path.isfile(os.path.join(self.proj_dir, "requirements_dev.txt")):
                    self.process_requirements(ret.dev_deps, 
                                    os.path.join(self.proj_dir, "requirements_dev.txt"))
            else:
                # No IVPM-specific data to rely on here
                return None

            # Attempt data migration to help people out...                
            msg.note("writing template ivpm.yaml file")
            with open(os.path.join(self.proj_dir, "ivpm.yaml"), "w") as fp:
                IvpmYamlWriter(OutWrapper(fp)).write(ret)
                
        return ret

    #********************************************************************
    # read_info
    #
    # Reads an .info file, which has the format key=value
    #********************************************************************
    def read_info(self, info_file):
        info = {}
    
        with open(info_file, "r") as fh:
            for l in fh.readlines():
                l = l.strip()
        
                comment_idx = l.find('#')
                
                if comment_idx != -1:
                    l = l[0:comment_idx]
        
                if l == '':
                    continue
        
                eq_idx = l.find('=')
                if eq_idx != -1:
                    key=l[0:eq_idx].strip()
                    src=l[eq_idx+1:len(l)].strip()
                    info[key] = src
                else:
                    print("Error: malformed line \"" + l + "\" in " + info_file);
     
        return info                
    
    def process_requirements(self, pkgs, req_path):
        with open(req_path, "r") as fp:
            reqs = requirements.parse(fp)
            
            for req in reqs:
#                print("req: " + str(req))
#                for e in dir(req):
#                    print("  %s : %s" % (e, str(req[e])))
                pkg = Package(req.name, None)
                pkg.pkg_type = PackageType.Python
                if not req.editable:
                    # PyPi package
                    pkg.src_type = SourceType.PyPi
                else:
                    # Editable
                    if req.uri is not None:
                        pkg.url = req.uri
                    else:
                        # Reverse engineer from the text line
                        pkg.url = req.line[len("-e"):].strip()
                        
                        hash_idx = pkg.url.rfind('#')
                        
                        if hash_idx != -1:
                            pkg.url = pkg.url[0:hash_idx]
                            
                    if pkg.url.startswith("git+"):
                        pkg.url = pkg.url[len("git+"):]
                        pkg.src_type = SourceType.Git
                    else:
                        fatal("unsupported Python URL %s" % pkg.url)
                        
                        
                if pkg.name in pkgs.keys():
                    fatal("duplicate package %s" % pkg.name)
                pkgs[pkg.name] = pkg
                        

        
    