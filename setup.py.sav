
import os
import stat
import sys
from setuptools import setup, find_namespace_packages
from setuptools.command.install import install

class InstallCmd(install):

  def run(self):
# install_lib
# ./lib/python3.6/site-packages/ivpm/scripts/git
#    install.run(self)
#    git_script = os.path.join(self.install_lib, "ivpm", "scripts", "git")
#    st = os.stat(git_script)
#    os.chmod(git_script, st.st_mode | stat.S_IEXEC)
    pass

rootdir = os.path.dirname(os.path.realpath(__file__))

version="1.1.3"

try:
   sys.path.insert(0, os.path.join(rootdir, "src/ivpm"))
   from __build_num__ import BUILD_NUM
   version += ".%s" % str(BUILD_NUM)
except ImportError as e:
   print("Failed to load build_num: %s" % str(e))

install_requires=[
    'setuptools',
    'pyyaml',
    'pyyaml-srcinfo-loader',
    'toposort',
    'httpx'
]

if sys.version_info < (3,10):
    install_requires.append('importlib_metadata')

if sys.version_info > (3,9):
    install_requires.append('jsonschema')


setup(
  name = "ivpm",
  version = version,
  packages=find_namespace_packages(where='src'),
  package_dir = {'' : 'src'},
  package_data = {'ivpm': ['scripts/*', 'templates/*', 'share/*', 'share/cmake/*']},
  author = "Matthew Ballance",
  author_email = "matt.ballance@gmail.com",
  description = ("IVPM (IP and Verification Package Manager) is a project-internal package manager."),
  long_description="""
  IVPM fetches Python and non-Python packages from package and source
  repositories. Python packages are installed into a local Python 
  virtual environment. Source packages are installed in editable mode
  """,
  license = "Apache 2.0",
  keywords = ["SystemVerilog", "Verilog", "RTL", "Coverage"],
  url = "https://github.com/fvutils/ivpm",
  entry_points={
    'console_scripts': [
      'ivpm = ivpm.__main__:main'
    ]
  },
  setup_requires=[
    'setuptools_scm',
  ],
  install_requires=install_requires,
  cmdclass={
    'install': InstallCmd
  },
)

