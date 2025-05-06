
import os
import subprocess
from .proj_info import ProjInfo


def ivpm_popen(cmd, **kwargs):
    """
    Wrapper around subprocess.Popen that configures paths from 
    the nearest IVPM project.
    """
    ivpm_project = getattr(kwargs, "ivpm_project", None)

    if ivpm_project is None:
        # Search up from the invocation location
        cwd = os.getcwd()
        while cwd is not None and cwd != "/" and ivpm_project is None:
            if os.path.exists(os.path.join(cwd, "ivpm.yaml")):
                ivpm_project = cwd
            else:
                cwd = os.path.dirname(cwd)
    
    if ivpm_project is not None:
        # Update environment variables
        proj_info = ProjInfo.mkFromProj(ivpm_project)

        if proj_info is None:
            raise Exception("Failed to read ivpm.yaml @ %s" % ivpm_project)
       
        if "env" in kwargs.keys() and kwargs["env"] is not None:
            env = kwargs["env"]
        else:
            env = os.environ.copy()
        env["IVPM_PROJECT"] = ivpm_project
        env["IVPM_PACKAGES"] = os.path.join(ivpm_project, "packages")

        # Add the virtual-environment path
        if "PATH" in env.keys():
            env["PATH"] = os.path.join(ivpm_project, "packages/python/bin") + ":" + env["PATH"]
        else:
            env["PATH"] = os.path.join(ivpm_project, "packages/python/bin")

        for es in proj_info.env_settings:
            es.apply(env)

        kwargs["env"] = env

    return subprocess.Popen(cmd, **kwargs)
