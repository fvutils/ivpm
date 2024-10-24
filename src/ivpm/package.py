'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import dataclasses as dc
from enum import Enum, auto
from typing import Dict, List, Set
from .update_info import UpdateInfo

class PackageType(Enum):
    Raw = auto()
    Python = auto()
    Unknown = auto()
    
PackageType2Spec = {
    PackageType.Raw     : "raw",
    PackageType.Python  : "python",
    PackageType.Unknown : "unknown"
    }

Spec2PackageType = {
    "raw"     : PackageType.Raw,
    "python"  : PackageType.Python,
    "unknown" : PackageType.Unknown
    }
    
class SourceType(Enum):
    Git = auto()
    Jar = auto()
    Tgz = auto()
    Txz = auto()
    Zip = auto()
    PyPi = auto()

Ext2SourceType = {
        ".git" : SourceType.Git,
        ".jar" : SourceType.Jar,
        ".tar.gz" : SourceType.Tgz,
        ".tar.xz" : SourceType.Txz,
        ".tgz" : SourceType.Tgz,
        ".zip" : SourceType.Zip
        }

SourceType2Ext = {
        SourceType.Git : ".git",
        SourceType.Jar : ".jar",
        SourceType.Tgz : ".tar.gz",
        SourceType.Txz : ".tar.xz",
        SourceType.Zip : ".zip"
    }

SourceType2Spec = {
        SourceType.Git  : "git",
        SourceType.Jar  : "jar",
        SourceType.Tgz  : "tgz",
        SourceType.Txz  : "txz",
        SourceType.Zip  : "zip",
        SourceType.PyPi : "pypi",
    }
Spec2SourceType = {
        "git"  : SourceType.Git,
        "jar"  : SourceType.Jar,
        "tgz"  : SourceType.Tgz,
        "txz"  : SourceType.Txz,
        "zip"  : SourceType.Zip,
        "pypi" : SourceType.PyPi
    }

@dc.dataclass
class Package(object):
    """Contains leaf-level information about a single package"""
    name : str
    srcinfo : object = None
    path : str = None
    pkg_type : PackageType = None
    src_type : str = None

    process_deps : bool = True
    setup_deps : Set[str] = dc.field(default_factory=set)
    dep_set : str = "default"
    proj_info : 'ProjInfo'= None

    def build(self, pkgs_info):
        pass

    def update(self, update_info : UpdateInfo) -> 'ProjInfo':
        from .project_info_reader import ProjectInfoReader
        from .proj_info import ProjInfo

        info = ProjectInfoReader(
            os.path.join(update_info.deps_dir, self.name)).read()
        
        return info
    
    def sync(self, pkgs_info):
        pass


