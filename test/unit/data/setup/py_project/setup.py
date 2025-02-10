
import os
from setuptools import setup, find_namespace_packages

version="0.0.1"

proj_dir = os.path.dirname(os.path.abspath(__file__))

isSrcBuild = False
try:
    from ivpm.setup import setup
    print("Found IVPM")
    isSrcBuild = os.path.isdir(os.path.join(proj_dir, "src"))
except ImportError as e:
    from setuptools import setup
    raise Exception("Failed to load IVPM: %s" % str(e))

setup_args = dict(
  name = "my-pkg",
  version=version,
  packages=find_namespace_packages(where='src'),
  package_dir = {'' : 'src'},
  author = "Matthew Ballance",
  author_email = "matt.ballance@gmail.com",
  description = "my_pkg",
  long_description="""
  my_pkg is a test project
  """,
  license = "Apache 2.0",
  keywords = ["test"],
  url = "https://github.com/fvutils/ivpm",
  setup_requires=[
    'setuptools_scm',
  ],
  install_requires=[],
)

setup(**setup_args)
