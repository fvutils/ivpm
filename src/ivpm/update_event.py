#****************************************************************************
#* update_event.py
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
Event types and listener interface for update operations.
"""
import dataclasses as dc
from enum import Enum, auto
from typing import Callable, List, Optional


class UpdateEventType(Enum):
    """Types of update events."""
    VENV_START = auto()         # Virtual environment creation started
    VENV_COMPLETE = auto()      # Virtual environment creation completed
    VENV_ERROR = auto()         # Virtual environment creation failed
    PACKAGE_START = auto()      # Package loading started
    PACKAGE_COMPLETE = auto()   # Package loading completed successfully
    PACKAGE_ERROR = auto()      # Package loading failed
    UPDATE_COMPLETE = auto()    # All packages loaded


@dc.dataclass
class UpdateEvent:
    """An update event."""
    event_type: UpdateEventType
    package_name: Optional[str] = None
    package_type: Optional[str] = None
    package_src: Optional[str] = None
    duration: Optional[float] = None
    cache_hit: Optional[bool] = None
    error_message: Optional[str] = None
    total_packages: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cacheable_packages: int = 0
    editable_packages: int = 0


class UpdateEventListener:
    """Interface for update event listeners."""
    
    def on_event(self, event: UpdateEvent):
        """Called when an update event occurs."""
        pass


class UpdateEventDispatcher:
    """Dispatches update events to registered listeners."""
    
    def __init__(self):
        self._listeners: List[UpdateEventListener] = []
    
    def add_listener(self, listener: UpdateEventListener):
        """Add a listener."""
        self._listeners.append(listener)
    
    def remove_listener(self, listener: UpdateEventListener):
        """Remove a listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)
    
    def dispatch(self, event: UpdateEvent):
        """Dispatch an event to all listeners."""
        for listener in self._listeners:
            listener.on_event(event)
