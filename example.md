Research FuseSoc carefully. We just added support for fetching FuseSoc packages and
identifying FuseSoc libraries in IVPM (../..). Now it's time to create an example that
showcases our work. Let's create two examples: one that specifies a FuseSoc package from the
registry ; one that clones a Git repo containing a FuseSoc-enabled IP. In both cases, we
want to show off the IVPM files, how IVPM manages setting up a Python venv (containg
FuseSoc), fetches tools, as well as the IP. Effectively, our pitch is 'ivpm update ; fusesoc
<X>'. Keep it simple, since we