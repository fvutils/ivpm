'''
Created on Jun 27, 2021

@author: mballance
'''
import os

from ivpm.msg import fatal
from ivpm.package import Package, Ext2SourceType, SourceType
from ivpm.packages_info import PackagesInfo
from lib2to3.pgen2.token import COLON


class PackagesMfReader(object):
    
    def __init__(self):
        pass
    
    def read(self, fp, name) -> PackagesInfo:
        ret = PackagesInfo(name)
    
        for l in fp.readlines():
            l = l.strip()
        
            comment_idx = l.find("#")
            if comment_idx != -1:
                l = l[0:comment_idx]
        
            if l == "":
                continue
        
            at_idx = l.find("@")
            options_m = {}
            if at_idx != -1:
                pkg_name=l[0:at_idx].strip()
                url=l[at_idx+1:len(l)].strip()
                
                pkg = Package(pkg_name, url)
                
                options = ""
                # Determine if there are options
                if url.find(" ") != -1:
                    options = url[url.find(" "):len(url)].strip()
                    pkg.url = url[:url.find(" ")].strip()
                elif url.find("\t") != -1:
                    options = url[url.find("\t"):len(url)].strip()
                    pkg.url = url[:url.find("\t")].strip()
                    
                ext = os.path.splitext(pkg.url)[1]
                
                if ext == ".gz" and pkg.url.endswith(".tar.gz"):
                    ext = ".tar.gz"
                elif ext == ".xz" and pkg.url.endswith(".tar.xz"):
                    ext = ".tar.xz"
                
                if not ext in Ext2SourceType.keys():
                    fatal("Unknown URL extension %s" % ext)
                else:
                    pkg.src_type = Ext2SourceType[ext]
                    
                if pkg.src_type == SourceType.Git:
                    # See if we need to change the form
                    if pkg.url.startswith("ssh://"):
                        # This is a key-based checkout supported in development
                        # mode. Change this to a regular http-based URL for now.
                        at_idx = pkg.url.find('@', len("ssh://"))
                        colon_idx = pkg.url.find(':', len("ssh://"))
                        
                        if at_idx != -1:
                            pkg.url = "https://" + pkg.url[:at_idx]
                        elif colon_idx != -1:
                            pkg.url = "https://" + pkg.url[len("ssh://"):colon_idx] + "/" + pkg.url[colon_idx+1:]

                if options != "":
                    for opt in options.split():
                        if opt.find("=") != -1:
                            opt_k = opt[:opt.find("=")].strip()
                            opt_v = opt[opt.find("=")+1:len(opt)].strip()
                            
                            if opt_k == "depth":
                                pkg.depth = opt_v
                            else:
                                fatal("unknown package option %s" % opt_k)
                        else:
                            fatal("malformed option \"" + opt + "\"")
    
            if pkg is not None:
                if pkg.name in ret.keys():
                    fatal("duplicate package entries for %s %s" % (pkg.name, name))
            
                ret.add_package(pkg)
    
        return ret        
