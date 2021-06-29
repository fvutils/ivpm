'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import stat
from string import Template


class CmdInit(object):
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        params = dict(
            name=args.name,
            version=args.version
        )

        # TODO: allow override    
        proj = os.getcwd()
    
        ivpm_dir = os.path.dirname(os.path.realpath(__file__))
        templates_dir = os.path.join(ivpm_dir, "templates")
    
        for src,dir in zip(["ivpm.yaml"], [""]):
        
            with open(os.path.join(templates_dir, src), "r") as fi:
                content = fi.read()
            
                outdir = os.path.join(proj, dir)
            
                if not os.path.isdir(outdir):
                    print("Note: Creating directory " + str(outdir))
                    os.mkdir(outdir)

                content_t = Template(content)
                content = content_t.safe_substitute(params)
            
                dest = os.path.join(proj, dir, src)
                if os.path.isfile(dest) and not args.force:
                    raise Exception("File " + str(dest) + " exists and --force not specified")
            
                with open(dest, "w") as fo:
                    fo.write(content)

        