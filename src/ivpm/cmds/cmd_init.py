'''
Created on Jun 8, 2021

@author: mballance
'''
import os
import sys


class CmdInit(object):
    """``ivpm init``: create a project from a template.

    Resolve the template (``-t``: a path, a URL, or a registry name), build the
    answers IVPM supplies, and render it with copier -- or, for the built-in
    ``default`` template when copier is not installed, with the small fallback
    renderer.  Imports are deferred: this module is loaded for every command.
    """

    def __call__(self, args):
        from ..init_template.errors import InitError
        from ..internal_error import EXIT_USER_ERROR
        try:
            self._run(args)
        except InitError as e:
            print("ivpm init: %s" % e, file=sys.stderr)
            sys.exit(EXIT_USER_ERROR)
        except KeyboardInterrupt:
            print("ivpm init: interrupted", file=sys.stderr)
            sys.exit(130)

    def _run(self, args):
        from ..init_template.template_rgy import InitTemplateRgy

        if getattr(args, "list", False):
            from ..show.show_init_templates import ShowInitTemplates
            ShowInitTemplates()(_ListArgs(args))
            return

        rgy = InitTemplateRgy.inst()
        dest = os.path.abspath(getattr(args, "project_dir", None) or os.getcwd())
        template = classify_template_arg(
            getattr(args, "template", None) or rgy.default_name(), rgy)

        for w in rgy.warnings():
            print("ivpm init: warning: %s" % w, file=sys.stderr)

        self._render(template, args, dest)

    def _render(self, template, args, dest):
        from ..init_template import copier_runner, fallback_runner
        from ..init_template.copier_runner import RunOptions
        from ..init_template.errors import InitError
        from ..init_template.template_vars import build_copier_inputs
        from ..init_template.trust import is_trusted

        force = getattr(args, "force", False)
        pretend = getattr(args, "pretend", False)

        # Today's safety check, kept for every template: never replace an
        # existing project file without -f.
        ivpm_yaml = os.path.join(dest, "ivpm.yaml")
        if os.path.isfile(ivpm_yaml) and not force:
            raise InitError("File %s exists and --force not specified" % ivpm_yaml)

        data, user_defaults = build_copier_inputs(template, args, dest)
        opts = RunOptions(
            # copier cannot prompt without a terminal (it fails fast), so
            # accept defaults and report any question that has none.
            defaults=getattr(args, "defaults", False) or not _stdin_is_tty(),
            overwrite=force,
            pretend=pretend,
            vcs_ref=getattr(args, "ref", None) or template.ref,
            skip_tasks=getattr(args, "skip_tasks", False),
            unsafe=is_trusted(template, getattr(args, "trust", False)),
            # The built-in 'default' writes one file, as 'ivpm init' always
            # has; keep it as quiet as the copier-less path.
            quiet=fallback_runner.supports(template) and not pretend)

        if copier_runner.copier_available():
            runner = copier_runner
        elif fallback_runner.supports(template):
            runner = fallback_runner
        else:
            raise InitError("Template '%s' needs copier, which is not installed."
                            % template.name, copier_runner.INSTALL_HINT)

        # The runner creates dest if needed, so a failed run leaves nothing.
        runner.run(template, dest, data, user_defaults, opts)

        if not pretend:
            print(template.next_steps
                  or "Next: add dependencies to ivpm.yaml, then run 'ivpm update'.")


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


class _ListArgs(object):
    """`ivpm init --list [--all]` expressed as `ivpm show init-templates` args."""

    def __init__(self, args):
        self.name = None
        self.all = getattr(args, "all", False)
        self.json = False
        self.no_rich = not sys.stdout.isatty()


def classify_template_arg(arg: str, rgy):
    """Resolve a ``-t`` value to an :class:`InitTemplate` (design §4).

    1. A local path: starts with ``.``, ``/`` or ``~``, or names an existing
       copier template directory (which therefore wins over a registry name
       spelled the same way).
    2. A URL or copier shorthand (``://``, ``gh:``, ``gl:``, ``git@``,
       ``git+``, ``*.git``), passed to copier as is.
    3. Otherwise a registry name.

    Templates given as a path or URL are untrusted unless ``--trust``.
    """
    from ..init_template import InitTemplate
    from ..init_template.errors import InitError
    from ..init_template.template_dir import is_template_dir, template_from_dir
    from ..init_template.template_info import is_remote_src

    explicit_path = arg.startswith((".", "/", "~")) or os.path.isabs(arg)
    path = os.path.abspath(os.path.expanduser(arg))
    if explicit_path or (not is_remote_src(arg) and is_template_dir(path)):
        if not os.path.isdir(path):
            raise InitError("Template directory %s does not exist" % path)
        if not is_template_dir(path):
            raise InitError("%s is not a copier template (no copier.yml)" % path)
        t = template_from_dir(path, origin="cli")
        t.trusted = False
        return t

    if is_remote_src(arg):
        return InitTemplate(name=arg, src=arg, origin="cli", trusted=False)

    t = rgy.get(arg)
    if t is None:
        close = rgy.suggest(arg)
        msg = "Unknown template '%s'" % arg
        if close:
            msg += " (did you mean: %s?)" % ", ".join(close)
        warnings = rgy.warnings()
        hint = "Run 'ivpm init --list' to see the available templates."
        if warnings:
            hint += "\n  Problems found while looking for templates:\n" + "\n".join(
                "    %s" % w for w in warnings)
        raise InitError(msg, hint)
    return t
