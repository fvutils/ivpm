'''
Cache management commands for IVPM

@author: generated
'''
import os
import stat
import sys
import time
from datetime import datetime
from ..cache import DirectoryCacheStore
from ..msg import note


def format_size(size_bytes: int) -> str:
    """Format size in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def format_age(ts) -> str:
    """Render a timestamp as 'YYYY-MM-DD (Nd ago)', or '-' when absent."""
    if ts is None:
        return "-"
    when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    days = int((time.time() - ts) // (24 * 60 * 60))
    return f"{when} ({days}d ago)"


class CmdCache:
    """Cache management command handler."""
    
    def __init__(self):
        pass
    
    def __call__(self, args):
        if args.cache_cmd == "init":
            self._init(args)
        elif args.cache_cmd == "info":
            self._info(args)
        elif args.cache_cmd == "clean":
            self._clean(args)
        elif args.cache_cmd in ("verify", "repair"):
            self._verify(args)
        else:
            print(f"Unknown cache command: {args.cache_cmd}", file=sys.stderr)
            sys.exit(1)
    
    def _init(self, args):
        """Initialize a new cache directory."""
        cache_dir = args.cache_dir
        
        if os.path.exists(cache_dir):
            if not os.path.isdir(cache_dir):
                print(f"Error: {cache_dir} exists and is not a directory", file=sys.stderr)
                sys.exit(1)
            if not args.force:
                print(f"Error: {cache_dir} already exists. Use --force to reinitialize.", file=sys.stderr)
                sys.exit(1)
        else:
            os.makedirs(cache_dir)
            note(f"Created cache directory: {cache_dir}")
        
        if args.shared:
            # Set setgid bit (g+s) so new files inherit group ownership
            current_mode = os.stat(cache_dir).st_mode
            os.chmod(cache_dir, current_mode | stat.S_ISGID)
            note(f"Set group inheritance (g+s) on {cache_dir}")
        
        print(f"Cache directory initialized: {cache_dir}")
        print(f"Set IVPM_CACHE={cache_dir} to enable caching")
    
    def _info(self, args):
        """Show information about the cache."""
        cache_dir = args.cache_dir
        
        if cache_dir is None:
            cache_dir = os.environ.get("IVPM_CACHE")
        
        if cache_dir is None:
            print("Error: No cache directory specified. Use --cache-dir or set IVPM_CACHE", file=sys.stderr)
            sys.exit(1)
        
        if not os.path.isdir(cache_dir):
            print(f"Error: Cache directory does not exist: {cache_dir}", file=sys.stderr)
            sys.exit(1)
        
        cache = DirectoryCacheStore(cache_dir)
        info = cache.get_cache_info()

        print(f"Cache directory: {cache_dir}")
        print(f"Total size: {format_size(info['total_size'])}")
        print(f"Packages: {len(info['packages'])}")
        print()

        for pkg in info['packages']:
            print(f"  {pkg['name']}:")
            print(f"    Versions: {len(pkg['versions'])}")
            print(f"    Size: {format_size(pkg['total_size'])}")

            if args.verbose:
                for ver in pkg['versions']:
                    print(f"      - {ver['version']}: {format_size(ver['size'])}")
                    print(f"          stored:      {format_age(ver.get('stored'))}")
                    print(f"          last linked: {format_age(ver.get('last_linked'))}")
    
    def _resolve_cache_dir(self, args, exit_code: int = 1) -> str:
        """The cache directory to operate on, or exit with a clear reason.

        ``verify`` exits 2 rather than 1 for these, because its other exit
        codes are statements about cache *health* and "you did not tell me
        which cache" is not one.
        """
        cache_dir = args.cache_dir
        if cache_dir is None:
            cache_dir = os.environ.get("IVPM_CACHE")
        if cache_dir is None:
            print("Error: No cache directory specified. Use --cache-dir or "
                  "set IVPM_CACHE", file=sys.stderr)
            sys.exit(exit_code)
        if not os.path.isdir(cache_dir):
            print(f"Error: Cache directory does not exist: {cache_dir}",
                  file=sys.stderr)
            sys.exit(exit_code)
        return cache_dir

    def _clean(self, args):
        """Clean old entries from the cache."""
        cache_dir = args.cache_dir

        if cache_dir is None:
            cache_dir = os.environ.get("IVPM_CACHE")

        if cache_dir is None:
            print("Error: No cache directory specified. Use --cache-dir or set IVPM_CACHE", file=sys.stderr)
            sys.exit(1)

        if not os.path.isdir(cache_dir):
            print(f"Error: Cache directory does not exist: {cache_dir}", file=sys.stderr)
            sys.exit(1)

        dry_run = getattr(args, "dry_run", False)
        cache = DirectoryCacheStore(cache_dir)
        removed = cache.clean_older_than(args.days, dry_run=dry_run)

        if dry_run:
            print(f"Would remove {removed} cache entries unused for more "
                  f"than {args.days} days")
        else:
            print(f"Removed {removed} cache entries unused for more "
                  f"than {args.days} days")

    # ----------------------------------------------------------------- verify

    def _verify(self, args):
        """``ivpm cache verify`` -- check a cache, or check and repair it.

        Two modes with genuinely different guarantees, not a CLI convenience:
        check never mutates and is safe on a cache you do not own, while repair
        evicts, reseals and removes.  One checker implementation serves both,
        so a repair can only ever act on something check would have reported.
        """
        from ..cache_verify import (Outcome, Status, repair_cache,
                                    verify_cache)
        from ..site_config import resolve_cache_verify_level

        cache_dir = self._resolve_cache_dir(args, exit_code=2)
        store = DirectoryCacheStore(cache_dir)
        level = resolve_cache_verify_level(
            "content" if getattr(args, "content", False) else None)
        if level == "off":
            # 'off' is a policy for update-time lookups; asking to verify and
            # then verifying nothing would be a silently useless run.
            level = "shape"
        package = getattr(args, "package", None)
        do_repair = (args.cache_cmd == "repair" or getattr(args, "repair", False))
        as_json = getattr(args, "json", False)

        report = None
        if do_repair:
            report = repair_cache(
                store, level, package=package,
                max_passes=getattr(args, "max_passes", 3),
                dry_run=getattr(args, "dry_run", False),
                upgrade=getattr(args, "upgrade", False),
                on_pass=None if as_json else self._make_pass_printer(
                    getattr(args, "dry_run", False)))
            result = report.final
        else:
            result = verify_cache(store, level, package=package)

        if as_json:
            import json
            payload = result.to_json()
            payload["mode"] = "repair" if do_repair else "check"
            payload["repair"] = report.to_json() if report else None
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self._print_health(result, report, verbose=getattr(args, "verbose", 0))

        sys.exit(self._verify_exit_code(result))

    @staticmethod
    def _verify_exit_code(result) -> int:
        """0 healthy, 1 degraded, 3 broken (2 is reserved for operational errors).

        The distinction that earns its keep is 1 vs. 3: 1 says "run --repair",
        3 says "wake a human".  Identical in both modes, so a cron job needs
        one rule.
        """
        from ..cache_verify import Status
        return {Status.HEALTHY: 0, Status.DEGRADED: 1, Status.BROKEN: 3}[
            result.status]

    @staticmethod
    def _make_pass_printer(dry_run: bool):
        def _print(p):
            if dry_run:
                print(f"pass {p.number}: would repair {p.repaired}")
            else:
                print(f"pass {p.number}: {p.repaired} repaired, "
                      f"{p.failed} failed")
        return _print

    @staticmethod
    def _print_repair_line(report):
        if report.dry_run:
            # Deliberately not reporting an outcome: later passes' findings
            # depend on repairs that were not applied, so anything past pass 1
            # would be fiction.
            print(f"  Dry run       would repair {report.repaired} problem(s) "
                  f"in one pass; nothing was changed")
        else:
            print(f"  Repair        {report.outcome.value} after "
                  f"{len(report.passes)} pass(es): {report.repaired} repaired, "
                  f"{report.failed} failed")

    def _print_health(self, result, report=None, verbose: int = 0):
        from ..cache_verify import REPAIR_MANUAL

        print(f"Cache health: {result.status.value:<12} {result.cache_dir}")
        print(f"  Inventory     {result.entries} entries across "
              f"{result.packages} packages, {format_size(result.total_bytes)}")
        print(f"                {result.sealed} sealed (manifest present) · "
              f"{result.legacy} legacy (no manifest)")
        print(f"  Verified      {result.entries_checked} entries at level "
              f"'{result.level}' ({format_size(result.bytes_hashed)} hashed) "
              f"in {result.elapsed_s:.1f}s")

        if not result.findings:
            print()
            if report is not None:
                self._print_repair_line(report)
            print("  No problems found.")
            return

        auto = sum(1 for f in result.findings if f.auto_repairable)
        manual = len(result.findings) - auto
        print()
        print(f"  Problems      {len(result.findings)} total · "
              f"{auto} auto-repairable · {manual} need manual action")
        by_problem = {}
        for f in result.findings:
            slot = by_problem.setdefault(f.problem.value, [0, f.repair, 0])
            slot[0] += 1
            slot[2] += f.reclaimable
        for name, (count, repair, size) in sorted(
                by_problem.items(), key=lambda kv: -kv[1][0]):
            extra = f"  ({format_size(size)})" if size else ""
            print(f"                {count:>3}  {name:<20} {repair:<9}{extra}")

        worst = sorted(result.by_package().items(), key=lambda kv: -kv[1])[:3]
        if worst:
            print()
            print("  Worst         " + f"\n{' ' * 16}".join(
                f"{name:<12} {n} problem(s)" for name, n in worst))

        if result.reclaimable_bytes:
            print()
            print(f"  Reclaimable   {format_size(result.reclaimable_bytes)} "
                  f"from residue and failed entries")

        print()
        if report is not None:
            self._print_repair_line(report)
        if auto and (report is None or report.dry_run):
            print(f"  Action        {auto} problem(s) repairable: "
                  f"ivpm cache verify --repair")
        if manual:
            uids = sorted({f.uid for f in result.findings
                           if not f.auto_repairable and f.uid is not None})
            who = (" (owned by uid %s)" % ", ".join(str(u) for u in uids)) if uids else ""
            print(f"                {manual} problem(s) need manual action{who}")

        if verbose:
            print()
            for f in result.findings:
                print("  " + f.message().replace("\n", "\n  "))
