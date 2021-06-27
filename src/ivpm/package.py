'''
Created on Jun 8, 2021

@author: mballance
'''
from enum import Enum, auto

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
    Zip = auto()
    PyPi = auto()

Ext2SourceType = {
        ".git" : SourceType.Git,
        ".jar" : SourceType.Jar,
        ".tar.gz" : SourceType.Tgz,
        ".tgz" : SourceType.Tgz
        }

SourceType2Ext = {
        SourceType.Git : ".git",
        SourceType.Jar : ".jar",
        SourceType.Tgz : ".tar.gz"
    }

SourceType2Spec = {
        SourceType.Git  : "git",
        SourceType.Jar  : "jar",
        SourceType.Tgz  : "tgz",
        SourceType.Zip  : "zip",
        SourceType.PyPi : "pypi",
    }
Spec2SourceType = {
        "git"  : SourceType.Git,
        "jar"  : SourceType.Jar,
        "tgz"  : SourceType.Tgz,
        "zip"  : SourceType.Zip,
        "pypi" : SourceType.PyPi
    }

class Package(object):
    """Contains leaf-level information about a single package"""
    
    def __init__(self, name, url=None):
        self.srcinfo = None
        self.name = name
        self.path = None
        self.pkg_type = PackageType.Raw
        self.src_type = None
        self.url = url
        self.branch = None
        self.tag = None
        self.version = None
        self.depth = None
        