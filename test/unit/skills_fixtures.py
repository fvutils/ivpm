"""Local, network-free Python environments that provide agent skills.

A venv is created ``--without-pip`` and distributions are written straight
into its site-packages as ``.dist-info`` directories -- which is all
``importlib.metadata`` needs -- so no build backend, index or network is
involved. ``make_wheel`` writes a real wheel for the tests that hand a
package to uv.
"""
import base64
import hashlib
import os
import subprocess
import sys
import zipfile


def skill_md(name, description=None):
    return "---\nname: %s\ndescription: %s\n---\nBody of %s\n" % (
        name, description or "Skill %s" % name, name)


def make_venv(path, python=None):
    """Create a pip-less venv; return (python, site_packages)."""
    subprocess.run([python or sys.executable, "-m", "venv", "--without-pip", path],
                   check=True, capture_output=True)
    if os.name == "nt":
        py = os.path.join(path, "Scripts", "python.exe")
        site = os.path.join(path, "Lib", "site-packages")
    else:
        py = os.path.join(path, "bin", "python")
        lib = os.path.join(path, "lib")
        site = os.path.join(lib, os.listdir(lib)[0], "site-packages")
    return py, site


def _record_line(root, rel):
    with open(os.path.join(root, rel), "rb") as fh:
        data = fh.read()
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return "%s,sha256=%s,%d" % (rel.replace(os.sep, "/"), digest, len(data))


def _entry_points_txt(entry_points):
    out = []
    for group, eps in entry_points.items():
        out.append("[%s]" % group)
        for name, value in eps.items():
            out.append("%s = %s" % (name, value))
        out.append("")
    return "\n".join(out)


def add_dist(site, name, version="1.0", files=None, entry_points=None, data=None):
    """Install a distribution into ``site``.

    ``files`` maps paths relative to site-packages to text; ``data`` maps
    paths relative to the environment prefix (e.g. share/agent-skills/x/SKILL.md)
    to text, the way a wheel's .data/data/ directory installs.
    """
    written = []
    for rel, text in (files or {}).items():
        path = os.path.join(site, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        written.append(rel)
    prefix = os.path.dirname(os.path.dirname(os.path.dirname(site)))
    for rel, text in (data or {}).items():
        path = os.path.join(prefix, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        written.append(os.path.relpath(path, site))

    norm = name.replace("-", "_")
    info = "%s-%s.dist-info" % (norm, version)
    info_dir = os.path.join(site, info)
    os.makedirs(info_dir, exist_ok=True)
    with open(os.path.join(info_dir, "METADATA"), "w") as fh:
        fh.write("Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (name, version))
    written.append(os.path.join(info, "METADATA"))
    if entry_points:
        with open(os.path.join(info_dir, "entry_points.txt"), "w") as fh:
            fh.write(_entry_points_txt(entry_points))
        written.append(os.path.join(info, "entry_points.txt"))
    lines = [_record_line(site, rel) if not rel.startswith("..") else
             "%s,," % rel.replace(os.sep, "/") for rel in written]
    lines.append("%s/RECORD,," % info)
    with open(os.path.join(info_dir, "RECORD"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return info_dir


def remove_dist(site, name, version):
    import shutil
    shutil.rmtree(os.path.join(site, "%s-%s.dist-info" % (name.replace("-", "_"), version)))


def skills_module(pkg, names, fn="get_skill_dirs"):
    """Source of ``<pkg>/skills.py`` returning share/skills/<name> dirs."""
    return (
        "import os\n"
        "def %s():\n"
        "    here = os.path.dirname(os.path.abspath(__file__))\n"
        "    return [os.path.join(here, 'share', 'skills', n) for n in %r]\n"
        % (fn, list(names)))


def skill_package(pkg, names, ep_name=None, version="1.0", dist=None):
    """(files, entry_points) for a package shipping ``names`` via one entry point."""
    files = {"%s/__init__.py" % pkg: "",
             "%s/skills.py" % pkg: skills_module(pkg, names)}
    for n in names:
        files["%s/share/skills/%s/SKILL.md" % (pkg, n)] = skill_md(n)
    eps = {"agent.skills": {ep_name or pkg: "%s.skills:get_skill_dirs" % pkg}}
    return files, eps


def make_wheel(out_dir, name, version, files, entry_points=None):
    """Write a pure-Python wheel and return its path."""
    norm = name.replace("-", "_")
    info = "%s-%s.dist-info" % (norm, version)
    path = os.path.join(out_dir, "%s-%s-py3-none-any.whl" % (norm, version))
    os.makedirs(out_dir, exist_ok=True)
    contents = dict(files)
    contents["%s/METADATA" % info] = "Metadata-Version: 2.1\nName: %s\nVersion: %s\n" % (
        name, version)
    contents["%s/WHEEL" % info] = ("Wheel-Version: 1.0\nGenerator: ivpm-tests\n"
                                   "Root-Is-Purelib: true\nTag: py3-none-any\n")
    if entry_points:
        contents["%s/entry_points.txt" % info] = _entry_points_txt(entry_points)
    record = []
    for rel, text in contents.items():
        data = text.encode()
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append("%s,sha256=%s,%d" % (rel, digest, len(data)))
    record.append("%s/RECORD,," % info)
    with zipfile.ZipFile(path, "w") as zf:
        for rel, text in contents.items():
            zf.writestr(rel, text)
        zf.writestr("%s/RECORD" % info, "\n".join(record) + "\n")
    return path
