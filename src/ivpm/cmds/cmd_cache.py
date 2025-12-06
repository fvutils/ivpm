'''
Cache management commands for IVPM

@author: generated
'''
import os
import stat
import sys
from ..cache import Cache
from ..msg import note


def format_size(size_bytes: int) -> str:
    """Format size in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


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
        
        cache = Cache(cache_dir)
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
        
        cache = Cache(cache_dir)
        removed = cache.clean_older_than(args.days)
        
        print(f"Removed {removed} cache entries older than {args.days} days")
