
import os, stat
from setuptools import setup
from setuptools.command.install import install

class InstallCmd(install):

  def run(self):
# install_lib
# ./lib/python3.6/site-packages/ivpm/scripts/git
    install.run(self)
    git_script = os.path.join(self.install_lib, "ivpm", "scripts", "git")
    st = os.stat(git_script)
    os.chmod(git_script, st.st_mode | stat.S_IEXEC)


setup(
  name = "ivpm",
  packages=['ivpm'],
  package_dir = {'' : 'src'},
  package_data = {'ivpm': ['scripts/*', 'templates/*']},
  author = "Matthew Ballance",
  author_email = "matt.ballance@gmail.com",
  description = ("IVPM (IP and Verification Package Manager) is a project-internal package manager."),
  license = "Apache 2.0",
  keywords = ["SystemVerilog", "Verilog", "RTL", "Coverage"],
  url = "https://github.com/mballance/ivpm",
  entry_points={
    'console_scripts': [
      'ivpm = ivpm.__main__:main'
    ]
  },
  setup_requires=[
    'setuptools_scm',
  ],
  install_requires=[
  ],
  cmdclass={
    'install': InstallCmd
  },
)

