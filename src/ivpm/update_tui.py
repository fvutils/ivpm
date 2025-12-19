#****************************************************************************
#* update_tui.py
#*
#* Copyright 2018-2024 Matthew Ballance and Contributors
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
"""
TUI (Text User Interface) for update operations.

Provides both Rich-based interactive display and transcript-based plain text output.
"""
import logging
import os
import sys
import time
from typing import Dict, List, Optional

from .update_event import UpdateEvent, UpdateEventListener, UpdateEventType

_logger = logging.getLogger("ivpm.tui")


class PackageStatus:
    """Tracks status of a package during update."""
    
    def __init__(self, name: str, pkg_type: str, pkg_src: str):
        self.name = name
        self.pkg_type = pkg_type or ""
        self.pkg_src = pkg_src or ""
        self.start_time = time.time()
        self.duration: Optional[float] = None
        self.cache_hit: Optional[bool] = None
        self.completed = False
        self.error: Optional[str] = None


class RichUpdateTUI(UpdateEventListener):
    """
    Rich-based TUI for update operations.
    
    Shows a stacked progress display with spinners for in-progress packages
    and checkmarks for completed packages.
    """
    
    def __init__(self, max_visible: Optional[int] = None):
        from rich.console import Console
        
        self.console = Console()
        self.packages: Dict[str, PackageStatus] = {}
        self.package_order: List[str] = []
        self.completed_packages: List[str] = []
        self.errors: List[tuple] = []  # (package_name, error_message)
        self.live = None
        self.max_visible = max_visible or self._get_terminal_rows() - 5
        self.total_packages = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.cacheable_packages = 0
        self.editable_packages = 0
    
    def _get_terminal_rows(self) -> int:
        """Get terminal height."""
        try:
            return os.get_terminal_size().lines
        except OSError:
            return 24
    
    def start(self):
        """Start the live display."""
        from rich.live import Live
        from rich.table import Table
        
        # Create initial empty table
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("status", width=2)
        table.add_column("name", style="bold")
        table.add_column("info")
        
        self.live = Live(table, console=self.console, refresh_per_second=10)
        self.live.start()
    
    def stop(self):
        """Stop the live display."""
        if self.live:
            self.live.stop()
            self.live = None
    
    def _render(self):
        """Render the current state."""
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text
        
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("status", width=2)
        table.add_column("name", style="bold")
        table.add_column("info")
        
        # Calculate which packages to show
        # Order: completed packages (oldest first) at top, in-progress at bottom
        visible_packages = []
        
        # Add completed packages (oldest first), up to max_visible minus in-progress count
        in_progress_count = sum(1 for name in self.package_order if not self.packages[name].completed)
        remaining_for_completed = self.max_visible - in_progress_count
        
        if remaining_for_completed > 0:
            # Take most recent completed packages (they'll be shown at top)
            completed_to_show = self.completed_packages[-remaining_for_completed:] if remaining_for_completed < len(self.completed_packages) else self.completed_packages
            visible_packages.extend(completed_to_show)
        
        # Add in-progress packages at the bottom
        for name in self.package_order:
            status = self.packages[name]
            if not status.completed:
                visible_packages.append(name)
        
        # Render each visible package
        for name in visible_packages:
            status = self.packages[name]
            
            if status.error:
                marker = Text("✗", style="bold red")
                info = Text(f"{status.pkg_type} {status.pkg_src}", style="red")
            elif status.completed:
                marker = Text("✓", style="bold green")
                cache_str = "C" if status.cache_hit else ""
                duration_str = f"{status.duration:.1f}s" if status.duration else ""
                info = Text(f"{status.pkg_type} {status.pkg_src} {cache_str} {duration_str}".strip())
            else:
                # Use Rich's Spinner for in-progress packages
                marker = Spinner("dots", style="bold cyan")
                info = Text(f"{status.pkg_type} {status.pkg_src}")
            
            table.add_row(marker, Text(name), info)
        
        return table
    
    def _update_display(self):
        """Update the live display."""
        if self.live:
            self.live.update(self._render())
    
    def on_event(self, event: UpdateEvent):
        """Handle an update event."""
        if event.event_type == UpdateEventType.VENV_START:
            # Add venv as a special "package" for display
            status = PackageStatus(
                "[venv]",
                "venv",
                "Creating Python virtual environment"
            )
            self.packages["[venv]"] = status
            self.package_order.append("[venv]")
            self._update_display()
        
        elif event.event_type == UpdateEventType.VENV_COMPLETE:
            if "[venv]" in self.packages:
                status = self.packages["[venv]"]
                status.completed = True
                status.duration = event.duration
                self.completed_packages.append("[venv]")
                self._update_display()
        
        elif event.event_type == UpdateEventType.VENV_ERROR:
            if "[venv]" in self.packages:
                status = self.packages["[venv]"]
                status.completed = True
                status.error = event.error_message
                self.errors.append(("[venv]", event.error_message))
                self._update_display()
        
        elif event.event_type == UpdateEventType.PACKAGE_START:
            status = PackageStatus(
                event.package_name,
                event.package_type,
                event.package_src
            )
            self.packages[event.package_name] = status
            self.package_order.append(event.package_name)
            self._update_display()
        
        elif event.event_type == UpdateEventType.PACKAGE_COMPLETE:
            if event.package_name in self.packages:
                status = self.packages[event.package_name]
                status.completed = True
                status.duration = event.duration
                status.cache_hit = event.cache_hit
                self.completed_packages.append(event.package_name)
                self._update_display()
        
        elif event.event_type == UpdateEventType.PACKAGE_ERROR:
            if event.package_name in self.packages:
                status = self.packages[event.package_name]
                status.completed = True
                status.error = event.error_message
                self.errors.append((event.package_name, event.error_message))
                self._update_display()
        
        elif event.event_type == UpdateEventType.UPDATE_COMPLETE:
            self.total_packages = event.total_packages
            self.cache_hits = event.cache_hits
            self.cache_misses = event.cache_misses
            self.cacheable_packages = event.cacheable_packages
            self.editable_packages = event.editable_packages
            self.stop()
            self._show_summary()
    
    def _show_summary(self):
        """Show a styled summary after update completes."""
        from rich.panel import Panel
        from rich.text import Text
        
        lines = []
        lines.append(f"Total packages: {self.total_packages}")
        lines.append(f"  Cacheable: {self.cacheable_packages}")
        lines.append(f"  Editable: {self.editable_packages}")
        if self.cacheable_packages > 0:
            lines.append(f"Cache hits: {self.cache_hits}")
            lines.append(f"Cache misses: {self.cache_misses}")
            hit_rate = (self.cache_hits / self.cacheable_packages * 100) if self.cacheable_packages > 0 else 0
            lines.append(f"Hit rate: {hit_rate:.1f}%")
        
        if self.errors:
            lines.append("")
            lines.append(Text("Errors:", style="bold red"))
            for pkg_name, error in self.errors:
                lines.append(Text(f"  {pkg_name}: {error}", style="red"))
            lines.append("")
            lines.append(Text("Re-run with --log-level=DEBUG for more details", style="yellow"))
        
        content = Text("\n".join(str(line) for line in lines))
        
        if self.errors:
            panel = Panel(content, title="Update Summary", border_style="red")
        else:
            panel = Panel(content, title="Update Summary", border_style="green")
        
        self.console.print(panel)


class TranscriptUpdateTUI(UpdateEventListener):
    """
    Transcript-based TUI for update operations.
    
    Shows plain text output suitable for non-interactive use or logging.
    """
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.errors: List[tuple] = []
    
    def on_event(self, event: UpdateEvent):
        """Handle an update event."""
        if event.event_type == UpdateEventType.VENV_START:
            print(">> [venv] - Creating Python virtual environment")
            sys.stdout.flush()
        
        elif event.event_type == UpdateEventType.VENV_COMPLETE:
            duration_str = f" ({event.duration:.1f}s)" if event.duration else ""
            print(f"<< [venv]{duration_str}")
            sys.stdout.flush()
        
        elif event.event_type == UpdateEventType.VENV_ERROR:
            print(f"<< [venv] ERROR: {event.error_message}")
            self.errors.append(("[venv]", event.error_message))
            sys.stdout.flush()
        
        elif event.event_type == UpdateEventType.PACKAGE_START:
            print(f">> {event.package_name} - {event.package_type or ''} {event.package_src or ''}")
            sys.stdout.flush()
        
        elif event.event_type == UpdateEventType.PACKAGE_COMPLETE:
            print(f"<< {event.package_name}")
            sys.stdout.flush()
        
        elif event.event_type == UpdateEventType.PACKAGE_ERROR:
            print(f"<< {event.package_name} ERROR: {event.error_message}")
            self.errors.append((event.package_name, event.error_message))
            sys.stdout.flush()
        
        elif event.event_type == UpdateEventType.UPDATE_COMPLETE:
            print("")
            print("Sub-Package Update Summary:")
            print(f"  Total packages: {event.total_packages}")
            print(f"    Cacheable: {event.cacheable_packages}")
            print(f"    Editable: {event.editable_packages}")
            if event.cacheable_packages > 0:
                print(f"  Cache hits: {event.cache_hits}")
                print(f"  Cache misses: {event.cache_misses}")
                hit_rate = (event.cache_hits / event.cacheable_packages * 100) if event.cacheable_packages > 0 else 0
                print(f"  Hit rate: {hit_rate:.1f}%")
            
            if self.errors:
                print("")
                print("Errors encountered:")
                for pkg_name, error in self.errors:
                    print(f"  {pkg_name}: {error}")
            sys.stdout.flush()


def create_update_tui(log_level: str) -> UpdateEventListener:
    """
    Create the appropriate TUI based on settings.
    
    Uses Rich-based TUI when:
    - log_level is NONE (no verbose output)
    - Output is to a TTY (interactive terminal)
    
    Uses transcript-based TUI when:
    - log_level is not NONE (verbose output requested)
    - Output is not to a TTY (redirected to file)
    
    Args:
        log_level: The configured log level (NONE, INFO, DEBUG, WARN)
        
    Returns:
        An UpdateEventListener implementation
    """
    use_rich = (
        log_level == "NONE" and 
        sys.stdout.isatty()
    )
    
    if use_rich:
        _logger.debug("Using Rich-based TUI")
        return RichUpdateTUI()
    else:
        verbose = log_level != "NONE"
        _logger.debug("Using transcript-based TUI (verbose=%s)", verbose)
        return TranscriptUpdateTUI(verbose=verbose)
