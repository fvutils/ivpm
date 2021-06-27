import os

def get_pkg_version(setup_py_path):
    """Returns the package version based on the etc/ivpm.info file"""
    rootdir = os.path.dirname(os.path.realpath(setup_py_path))

    version=None
    with open(os.path.join(rootdir, "etc", "ivpm.info"), "r") as fp:
        while True:
            l = fp.readline()
            if l == "":
                break
            if l.find("version=") != -1:
                version=l[l.find("=")+1:].strip()
                break

    if version is None:
        raise Exception("Failed to find version in ivpm.info")

    if "BUILD_NUM" in os.environ.keys():
        version += "." + os.environ["BUILD_NUM"]

    return version



