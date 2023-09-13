import os
import sys

class CmdShare(object):

    def __call__(self, args):
        ivpm_cmd_dir = os.path.dirname(os.path.abspath(__file__))
        ivpm_dir = os.path.dirname(ivpm_cmd_dir)
        ivpm_sharedir = os.path.join(ivpm_dir, "share")

        if args.path is not None:
            for p in args.path:
                ivpm_sharedir = os.path.join(ivpm_sharedir, p)
        sys.stdout.write(ivpm_sharedir)
#        print(ivpm_sharedir)
