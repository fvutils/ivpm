'''
Created on Jun 22, 2021

@author: mballance
'''
import sys


def note(msg):
    print("Note: %s" % msg)
    sys.stdout.flush()

def warning(msg):
    print("Warning: %s" % msg)
    sys.stdout.flush()
        
def error(msg):
    print("Error: %s" % msg)
    sys.stdout.flush()
    
def fatal(msg):
    print("Fatal: %s" % msg)
    raise Exception(msg)
