#*****************************************************************************
#* srcinfo.py
#*****************************************************************************

class SrcInfo(object):
    
    def __init__(self,
                 filename=None,
                 lineno=-1,
                 linepos=-1):
        self.filename = filename
        self.lineno   = lineno
        self.linepos  = linepos
        
    def __str__(self):
        if self.filename is not None:
            return "%s:%d:%d" % (self.filename,self.lineno,self.linepos)
        else:
            return "%d:%d" % (self.lineno,self.linepos)
        
class SrcInfoDict(dict):        
    pass

def dict_update(self, v):
    dict.update(self,v)
    self.srcinfo = v.srcinfo
    
SrcInfoDict.update = dict_update

class SrcInfoList(list):
    pass
        
class SrcInfoInt(int):
    pass
    
class SrcInfoStr(str):
    pass
    
    
