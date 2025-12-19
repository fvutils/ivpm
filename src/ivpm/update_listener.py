#****************************************************************************
#* update_listener.py
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
#* Created on:
#*     Author: 
#*
#****************************************************************************
import dataclasses as dc
from typing import Optional
from abc import ABC, abstractmethod


@dc.dataclass
class PackageUpdateEvent:
    """Information about a package update event."""
    name: str
    is_editable: bool = False
    is_cacheable: bool = False
    cache_hit: Optional[bool] = None  # None if not applicable
    error: Optional[str] = None


class UpdateListener(ABC):
    """Interface for receiving update events during package fetching."""

    @abstractmethod
    def on_package_start(self, event: PackageUpdateEvent):
        """Called when a package update starts.
        
        Args:
            event: Information about the package being updated
        """
        pass

    @abstractmethod
    def on_package_finish(self, event: PackageUpdateEvent):
        """Called when a package update finishes.
        
        Args:
            event: Information about the completed update, including
                   cache_hit status and any error that occurred
        """
        pass


class DefaultUpdateListener(UpdateListener):
    """Default listener that prints update progress."""
    
    def on_package_start(self, event: PackageUpdateEvent):
        attrs = []
        if event.is_editable:
            attrs.append("editable")
        if event.is_cacheable:
            attrs.append("cacheable")
        attr_str = f" [{', '.join(attrs)}]" if attrs else ""
        print(f"[START] {event.name}{attr_str}")

    def on_package_finish(self, event: PackageUpdateEvent):
        if event.error:
            print(f"[FAIL]  {event.name}: {event.error}")
        else:
            cache_info = ""
            if event.cache_hit is True:
                cache_info = " (cache hit)"
            elif event.cache_hit is False:
                cache_info = " (cache miss)"
            print(f"[DONE]  {event.name}{cache_info}")
