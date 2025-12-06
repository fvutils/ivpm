'''
Created on May 29, 2021

@author: mballance
'''
from yaml.composer import Composer
from yaml.constructor import FullConstructor
from yaml.parser import Parser
from yaml.reader import Reader
from yaml.resolver import Resolver
from yaml.scanner import Scanner

from yaml_srcinfo_loader.srcinfo import SrcInfoInt, SrcInfoStr, SrcInfo, \
    SrcInfoDict, SrcInfoList


class Loader(Reader, Scanner, Parser, Composer, FullConstructor, Resolver):
    """YAML loader that annotates lineno/linepos information onto returned elements"""
    
    DEBUG = 0
    
    class LineDict(dict):
        pass
    
    def __init__(self, stream):
        Reader.__init__(self, stream)
        Scanner.__init__(self)
        Parser.__init__(self)
        Composer.__init__(self)
        FullConstructor.__init__(self)
        Resolver.__init__(self)

        Loader.add_constructor(
            'tag:yaml.org,2002:map', 
            Loader.construct_yaml_map)
        
        self.filename = None

        if hasattr(stream, "name"):
            self.filename = stream.name
        
    def compose_node(self, parent, index):
        # Stash the associated locations on the YAML node
        ret = Composer.compose_node(self, parent, index)
        ret.__lineno__ = self.line
        ret.__linepos__ = self.column
        return ret
   
#     def construct_object(self, node, deep=False):
#         print("--> construct_object")
#         obj = FullConstructor.construct_object(self, node, deep=deep)
#         print("obj: " + str(type(obj)) + " " + str(obj))
#        
#         if isinstance(obj,dict):
#             ret = SrcInfoDict()
# #            self._add_srcinfo(node, ret)
# 
#             for k in obj.keys():
#                 print("Add key " + str(k))
#                 ret[k] = obj[k]
#         else:
#             ret = obj
#         print("<-- construct_object")
#         
#         return ret

    def construct_yaml_map(self, node):
        if Loader.DEBUG > 0:
            print("construct_yaml_map")
        data = SrcInfoDict()
        yield data
        value = self.construct_mapping(node)
        if Loader.DEBUG > 0:
            print("data.update")
        data.update(value)

    def construct_mapping(self, node, deep=False):
        obj = FullConstructor.construct_mapping(self, node, deep=deep)
        ret = SrcInfoDict()
        self._add_srcinfo(node, ret)
        
        for k in obj.keys():
            ret[k] = obj[k]
            
        if Loader.DEBUG > 0:
            print("construct_mapping: " + str(ret) + " " + str(type(ret)))
            print("srcinfo: " + str(ret.srcinfo) + " type: " + str(type(ret)))
#        raise Exception("construct_mapping")
        return ret
    
    def construct_sequence(self, node, deep=False):
        obj = FullConstructor.construct_sequence(self, node, deep=deep)
        ret = SrcInfoList()
        self._add_srcinfo(node, ret)
        
        for e in obj:
            ret.append(e)
        return ret
    
    def construct_scalar(self, node):
        # This returns an integer
        ret = FullConstructor.construct_scalar(self, node)
        if isinstance(ret,int):
            ret = SrcInfoInt(ret)
        elif type(ret) == str:
            ret = SrcInfoStr(ret)
        else:
            raise Exception("Unsupported element-type " + str(type(ret)))

        self._add_srcinfo(node, ret)            
        return ret
    
    def _add_srcinfo(self, node, obj):
        if hasattr(node, "__lineno__"):
            obj.srcinfo = SrcInfo(
                self.filename,
                node.__lineno__,
                node.__linepos__)
        else:
            obj.srcinfo = SrcInfo()

