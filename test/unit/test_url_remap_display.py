"""
Tests that a rewritten fetch URL is visible in the update display.

A ``git-url-map`` rule (or an auth-order https->ssh rewrite) redirects a fetch
to a different host than the one written in ivpm.yaml.  That must not happen
silently, so both TUIs surface the redirect: the Rich TUI on a continuation
line under the package row, the transcript TUI as its own pair of lines.
Packages fetched exactly as declared must render as they always have.
"""
import io
import unittest
from contextlib import redirect_stdout

from rich.console import Console

from ivpm.update_event import UpdateEvent, UpdateEventType
from ivpm.update_tui import RichUpdateTUI, TranscriptUpdateTUI


DECLARED = "https://github.com/fvutils/svdep.git"
EFFECTIVE = "ssh://git@mirror.example.net:222/fvutils/svdep.git"


def _render(tui, width=120):
    """Render *tui*'s table to plain text."""
    console = Console(width=width, file=io.StringIO(), force_terminal=False)
    console.print(tui._render())
    return console.file.getvalue()


def _start(tui, name, src):
    tui.on_event(UpdateEvent(
        event_type=UpdateEventType.PACKAGE_START,
        package_name=name, package_type="git", package_src=src))


def _resolved(tui, name, src, effective):
    tui.on_event(UpdateEvent(
        event_type=UpdateEventType.PACKAGE_SRC_RESOLVED,
        package_name=name, package_src=src, package_src_effective=effective))


def _complete(tui, name, **kw):
    tui.on_event(UpdateEvent(
        event_type=UpdateEventType.PACKAGE_COMPLETE, package_name=name, **kw))


class TestRichUpdateTUIRemapDisplay(unittest.TestCase):

    def test_remapped_package_shows_both_urls(self):
        """A redirected fetch shows the declared URL and the effective one."""
        tui = RichUpdateTUI()
        _start(tui, "svdep", DECLARED)
        _resolved(tui, "svdep", DECLARED, EFFECTIVE)
        _complete(tui, "svdep", duration=0.2, version="main")

        out = _render(tui)
        self.assertIn(DECLARED, out)
        self.assertIn(EFFECTIVE, out)
        self.assertIn("→", out)

    def test_unmapped_package_is_single_line(self):
        """No rewrite -> no arrow, no continuation line (unchanged display)."""
        tui = RichUpdateTUI()
        _start(tui, "sby", "https://github.com/YosysHQ/sby.git")
        _complete(tui, "sby", duration=0.4, version="main")

        out = _render(tui)
        self.assertIn("https://github.com/YosysHQ/sby.git", out)
        self.assertNotIn("→", out)
        body = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(1, len(body))

    def test_remap_visible_while_fetch_in_progress(self):
        """The redirect shows during the clone, not only after it finishes --
        that is when the user most needs to see where bytes are coming from."""
        tui = RichUpdateTUI()
        _start(tui, "svdep", DECLARED)
        _resolved(tui, "svdep", DECLARED, EFFECTIVE)

        out = _render(tui)
        self.assertIn(EFFECTIVE, out)

    def test_remap_survives_progress_message(self):
        """A git progress message replaces the source on the first line, but
        the redirect line must remain."""
        tui = RichUpdateTUI()
        _start(tui, "svdep", DECLARED)
        _resolved(tui, "svdep", DECLARED, EFFECTIVE)
        tui.packages["svdep"].progress_message = "Receiving objects 42%"

        out = _render(tui)
        self.assertIn("Receiving objects 42%", out)
        self.assertIn(EFFECTIVE, out)

    def test_resolved_event_for_unknown_package_is_ignored(self):
        """An event naming a package with no row must not raise."""
        tui = RichUpdateTUI()
        _resolved(tui, "never-started", DECLARED, EFFECTIVE)
        self.assertNotIn(EFFECTIVE, _render(tui))


class TestTranscriptUpdateTUIRemapDisplay(unittest.TestCase):

    def test_remap_emits_both_urls(self):
        tui = TranscriptUpdateTUI()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _start(tui, "svdep", DECLARED)
            _resolved(tui, "svdep", DECLARED, EFFECTIVE)
        out = buf.getvalue()
        self.assertIn(DECLARED, out)
        self.assertIn("→ %s" % EFFECTIVE, out)

    def test_no_remap_emits_nothing_extra(self):
        tui = TranscriptUpdateTUI()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _start(tui, "sby", "https://github.com/YosysHQ/sby.git")
        self.assertNotIn("→", buf.getvalue())


class TestPackageGitEmitsResolvedEvent(unittest.TestCase):
    """The event must be dispatched once per package, from the git package
    itself, so no extra URL resolution work is added to the update path."""

    def _pkg_and_dispatcher(self, url, map_rules):
        from ivpm.pkg_types.package_git import PackageGit

        dispatched = []

        class _Dispatcher:
            def dispatch(self, ev):
                dispatched.append(ev)

        class _UpdateInfo:
            args = None
            event_dispatcher = _Dispatcher()

        pkg = PackageGit("svdep")
        pkg.url = url
        return pkg, _UpdateInfo(), dispatched

    def test_rewrite_dispatches_once(self):
        """_get_effective_url runs more than once per fetch (ls-remote, then
        clone); the redirect must be reported a single time."""
        import os
        pkg, info, dispatched = self._pkg_and_dispatcher(DECLARED, None)
        os.environ["IVPM_GIT_URL_MAP"] = "%s=%s" % (
            "https://github.com/fvutils/", "ssh://git@mirror.example.net:222/fvutils/")
        try:
            from ivpm import site_config
            site_config.reset_site_config()
            first = pkg._get_effective_url(info)
            second = pkg._get_effective_url(info)
        finally:
            del os.environ["IVPM_GIT_URL_MAP"]
            from ivpm import site_config
            site_config.reset_site_config()

        self.assertEqual(first, second)
        # Assert the map rule specifically -- an ssh auth rewrite alone would
        # also change the URL, and would not prove the rule fired.
        self.assertIn("mirror.example.net", first)
        events = [e for e in dispatched
                  if e.event_type == UpdateEventType.PACKAGE_SRC_RESOLVED]
        self.assertEqual(1, len(events))
        self.assertEqual(DECLARED, events[0].package_src)
        self.assertEqual(first, events[0].package_src_effective)

    def test_no_rewrite_dispatches_nothing(self):
        pkg, info, dispatched = self._pkg_and_dispatcher(
            "https://github.com/YosysHQ/sby.git", None)
        pkg.anonymous = True   # keep the URL exactly as declared
        pkg._get_effective_url(info)
        events = [e for e in dispatched
                  if e.event_type == UpdateEventType.PACKAGE_SRC_RESOLVED]
        self.assertEqual([], events)


if __name__ == "__main__":
    unittest.main()
