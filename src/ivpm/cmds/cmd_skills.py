#****************************************************************************
#* cmd_skills.py
#*
#* Copyright 2026 Matthew Ballance and Contributors
#*
#* Licensed under the Apache License, Version 2.0 (the "License"); you may
#* not use this file except in compliance with the License.
#* You may obtain a copy of the License at:
#*
#*   http://www.apache.org/licenses/LICENSE-2.0
#*
#* Unless required by applicable law or agreed to in writing, software
#* distributed under the License is distributed on an "AS IS" BASIS,
#* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#* See the License for the specific language governing permissions and
#* limitations under the License.
#*
#****************************************************************************
"""``ivpm skills``: install agent skills from a Python environment a la carte."""
import argparse
import json
import os
import sys

EPILOG = """\
Skills come from Python packages that register the 'agent.skills' entry-point
group or install share/agent-skills/<name>/SKILL.md. See the 'Agent skills'
pages of the IVPM documentation for the contract.

Examples:
  uvx ivpm skills install --with pssparser --all
  ivpm skills list
  ivpm skills install pssparser-api --agent claude
  ivpm skills sync
"""


def add_skills_parser(subparser, finalize):
    """Register 'ivpm skills' and its subcommands."""
    skills_cmd = subparser.add_parser(
        "skills",
        help="Install agent skills from Python packages into a directory",
        description="Install agent skills from Python packages into a directory, "
                    "for the agents you use. Works in any directory with a Python "
                    "environment; no ivpm.yaml needed.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = skills_cmd.add_subparsers(dest="skills_cmd")
    sub.required = True

    def common(p, env=True):
        p.add_argument("-d", "--dir", dest="skills_dir", default=None,
                       help="Target directory (default: current directory)")
        if env:
            p.add_argument("--python", default=None,
                           help="Interpreter whose environment is queried")
            p.add_argument("--with", dest="with_specs", action="append", default=[],
                           metavar="SPEC",
                           help="Build the environment with uv from this package "
                                "spec (repeatable); skills are then copied")
            p.add_argument("--offline", action="store_true", default=False,
                           help="With --with: do not access the network")
        p.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON")
        p.add_argument("--no-rich", dest="no_rich", action="store_true", default=False,
                       help="Plain-text output without Rich formatting")

    p = sub.add_parser("list", help="List the skills the environment offers")
    p.add_argument("selectors", nargs="*", metavar="SELECTOR",
                   help="Only show skills matching these selectors")
    common(p)

    p = sub.add_parser("status", help="Show installed skills and problems")
    common(p)

    for name, aliases in (("install", ["add"]),):
        p = sub.add_parser(name, aliases=aliases,
                           help="Install skills (and record them in .agents/ivpm-skills.json)")
        p.add_argument("selectors", nargs="*", metavar="SELECTOR",
                       help="Skill name, entry-point name, <ep>/<skill>, or "
                            "dist:<distribution>; globs allowed")
        p.add_argument("--all", dest="all", action="store_true", default=False,
                       help="Install every skill the environment offers")
        p.add_argument("--agent", dest="agents", action="append", default=None,
                       metavar="LIST",
                       help="Comma list of agents, claude, cursor, or all "
                            "(default: all, or what the state file records)")
        mode = p.add_mutually_exclusive_group()
        mode.add_argument("--copy", dest="mode", action="store_const", const="copy",
                          default=None, help="Copy skill directories")
        mode.add_argument("--link", dest="mode", action="store_const", const="link",
                          help="Symlink skill directories (default)")
        p.add_argument("--as", dest="as_name", default=None, metavar="NAME",
                       help="Install a single skill under another name")
        common(p)

    p = sub.add_parser("uninstall", aliases=["remove"],
                       help="Remove skills installed by 'ivpm skills'")
    p.add_argument("selectors", nargs="*", metavar="SELECTOR")
    p.add_argument("--all", dest="all", action="store_true", default=False,
                   help="Remove every skill 'ivpm skills' installed here")
    common(p, env=False)

    p = sub.add_parser("sync",
                       help="Re-install the recorded selections from the current environment")
    p.add_argument("--upgrade", action="store_true", default=False,
                   help="With a --with environment: let uv pick newer versions")
    common(p)

    # Aliases stay usable but out of the help listing
    finalize(sub, ("add", "remove"))
    skills_cmd.set_defaults(func=CmdSkills())
    return skills_cmd


class CmdSkills(object):

    def __call__(self, args):
        from ..agent_skills.manager import SkillsError
        cmd = {"add": "install", "remove": "uninstall"}.get(args.skills_cmd, args.skills_cmd)
        root = os.path.abspath(args.skills_dir or os.getcwd())
        try:
            getattr(self, "_%s" % cmd)(root, args)
        except SkillsError as exc:
            print("ivpm skills: error: %s" % exc, file=sys.stderr)
            sys.exit(1)

    # ------------------------------------------------------------------ #

    def _query(self, root, args, for_state=False):
        from ..agent_skills import manager
        if for_state:
            choice = manager.sync_choice(root, args.python, args.with_specs,
                                         upgrade=getattr(args, "upgrade", False),
                                         offline=args.offline)
        else:
            choice = manager.choose_env(root, args.python, args.with_specs,
                                        offline=args.offline)
        if not args.json and choice.with_specs:
            self._note(args, "Building the environment with uv (%s)..." % choice.label)
        return manager.query(choice)

    def _note(self, args, msg):
        print(msg, file=sys.stderr if args.json else sys.stdout)

    def _env_line(self, info):
        return "Python: %s (%s)" % (info.result.python or info.choice.label, info.choice.reason)

    def _print_errors(self, info):
        for err in info.result.errors:
            print("warning: %s" % err, file=sys.stderr)

    # list --------------------------------------------------------------- #

    def _list(self, root, args):
        from ..agent_skills import manager, select, state
        info = self._query(root, args, for_state=state.load_quiet(root) is not None
                           and not args.python and not args.with_specs)
        avail = info.available
        if args.selectors:
            avail = select.select(args.selectors, avail)
        st = state.load_quiet(root)
        owners = manager.ownership(root, st)

        rows = []
        for av in avail:
            sel = st.find(av.key) if st else None
            dest = sel.dest_name if sel else av.name
            where = []
            for key, names in owners.items():
                if dest in names:
                    where.append((key[:-len("_skills")], names[dest]))
            rows.append((av, dest, where))

        if args.json:
            print(json.dumps({
                "python": info.result.python, "reason": info.choice.reason,
                "skills": [{
                    "name": av.name, "kind": av.kind, "provider": av.provider,
                    "dist": av.dist, "version": av.version, "path": av.path,
                    "description": av.description, "installed_as": dest if where else None,
                    "installed": [{"agent": a, "owner": o} for a, o in where],
                } for av, dest, where in rows],
                "errors": [str(e) for e in info.result.errors],
            }, indent=2))
            return

        def installed_col(dest, where, av):
            if not where:
                return "-"
            agents = ", ".join(a for a, _ in where)
            owner_set = sorted({o for _, o in where})
            txt = "%s (%s)" % (agents, ", ".join(owner_set))
            if dest != av.name:
                txt = "as %s: %s" % (dest, txt)
            return txt

        def dist_col(av):
            return "%s %s" % (av.dist, av.version or "") if av.dist else "-"

        if args.no_rich:
            print(self._env_line(info))
            for av, dest, where in rows:
                print("%-28s %-22s %-28s %s" % (av.name, av.provider, dist_col(av).strip(),
                                                 installed_col(dest, where, av)))
        else:
            from rich.table import Table
            from rich import box
            from ..tui_theme import make_console
            console = make_console()
            console.print(self._env_line(info))
            table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
            table.add_column("Skill", style="cyan bold")
            table.add_column("Provider")
            table.add_column("Dist")
            table.add_column("Installed")
            for av, dest, where in rows:
                name = av.name if av.kind == "skill" else "%s (plugin)" % av.name
                table.add_row(name, av.provider, dist_col(av).strip(),
                              installed_col(dest, where, av))
            console.print(table)
        if not rows:
            print("No skills found." if not args.selectors else "No skills match.")
        self._print_errors(info)

    # status ------------------------------------------------------------- #

    def _status(self, root, args):
        from ..agent_skills import manager, state
        st = state.load(root) if os.path.isfile(state.state_path(root)) else None
        info = None
        if st is not None and st.selections:
            try:
                info = self._query(root, args, for_state=True)
            except manager.SkillsError as exc:
                print("warning: %s" % exc, file=sys.stderr)
        st, problems = manager.status(root, info)

        if args.json:
            print(json.dumps({
                "state": st.to_json() if st else None,
                "python": info.result.python if info else None,
                "problems": [p.__dict__ for p in problems],
            }, indent=2))
            return

        if st is None:
            print("No skills installed by 'ivpm skills' in %s" % root)
            return
        if info is not None:
            print(self._env_line(info))
        modes = sorted({e.mode for v in st.installed.values() for e in v})
        mode_txt = st.mode
        if modes and modes != [st.mode]:
            mode_txt = "%s (installed as %s)" % (st.mode, "/".join(modes))
        print("Agents: %s    Mode: %s%s" % (
            ", ".join(st.agents), mode_txt,
            ("    With: %s" % " ".join(st.with_specs)) if st.with_specs else ""))
        for sel in st.selections:
            where = [k[:-len("_skills")] for k, v in sorted(st.installed.items())
                     if any(e.name == sel.dest_name for e in v)]
            as_txt = " as %s" % sel.as_name if sel.as_name else ""
            print("  %-28s %s/%s%s  [%s]" % (sel.dest_name, sel.provider, sel.skill,
                                             as_txt, ", ".join(where) or "not installed"))
        if problems:
            print("Problems:")
            for p in problems:
                tgt = " (%s)" % p.target[:-len("_skills")] if p.target else ""
                print("  %s%s: %s: %s" % (p.name, tgt, p.kind, p.detail))
        else:
            print("No problems found.")

    # install / uninstall / sync ------------------------------------------ #

    def _report(self, args, info, report, verb):
        if args.json:
            print(json.dumps({
                "python": info.result.python,
                "mode": report.mode,
                "installed": [{"name": d, "provider": av.provider, "dist": av.dist,
                               "version": av.version} for d, av in report.installed],
                "missing": [s.to_json() for s in report.missing],
                "version_changes": {d: list(v) for d, v in report.version_changes.items()},
                "targets": report.targets,
            }, indent=2))
            return
        print(self._env_line(info))
        if report.copy_reason:
            print("Copying rather than linking: %s." % report.copy_reason)
        for dest, av in report.installed:
            ver = " %s" % av.version if av.version else ""
            print("  %s %s  (%s, %s%s)" % (verb, dest, av.provider, av.dist or "-", ver))
        for d, (old, new) in sorted(report.version_changes.items()):
            print("  %s: %s -> %s" % (d, old, new))
        for sel in report.missing:
            print("  warning: %s/%s is no longer provided; left as it was"
                  % (sel.provider, sel.skill))
        rel = [os.path.relpath(t) for t in report.targets]
        print("%d skill(s) in %s (%s)" % (len(report.installed), ", ".join(rel),
                                          "copies" if report.mode == "copy" else "symlinks"))
        self._print_errors(info)

    def _install(self, root, args):
        from ..agent_skills import manager
        if not args.all and not args.selectors:
            raise manager.SkillsError("name the skills to install, or pass --all "
                                      "(see 'ivpm skills list')")
        info = self._query(root, args, for_state=True)
        report = manager.install(root, info, args.selectors, all_=args.all,
                                 agents=args.agents, mode=args.mode,
                                 as_name=args.as_name,
                                 cache_dir=manager.uv_cache_dir())
        self._report(args, info, report, "installed")

    def _uninstall(self, root, args):
        from ..agent_skills import manager
        if not args.all and not args.selectors:
            raise manager.SkillsError("name the skills to uninstall, or pass --all")
        removed = manager.uninstall(root, args.selectors, all_=args.all)
        if args.json:
            print(json.dumps({"removed": [s.to_json() for s in removed]}, indent=2))
            return
        for sel in removed:
            print("  removed %s" % sel.dest_name)

    def _sync(self, root, args):
        from ..agent_skills import manager, state
        if state.load(root) is None:
            raise manager.SkillsError("no %s in %s; nothing to sync"
                                      % (state.STATE_REL, root))
        info = self._query(root, args, for_state=True)
        report = manager.sync(root, info, cache_dir=manager.uv_cache_dir())
        self._report(args, info, report, "synced")
