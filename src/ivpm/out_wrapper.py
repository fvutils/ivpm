'''
Created on Jun 27, 2021

@author: mballance
'''

class OutWrapper(object):
    
    def __init__(self, out):
        self.out = out
        self.ind = ""
        
    def inc_indent(self):
        self.ind += "    "
        
    def dec_indent(self):
        if len(self.ind) > 4:
            self.ind = self.ind[0:-4]
        else:
            self.ind = ""
        
    def println(self, fmt, *args):
        self.out.write(self.ind)
        fmt_args = tuple(args)
        self.out.write(fmt % fmt_args)
        self.out.write("\n")
        pass
    
    def write(self, fmt, *args):
        fmt_args = tuple(args)
        self.out.write(fmt % fmt_args)
        