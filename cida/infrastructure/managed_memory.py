from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ManagedMemoryAccounting:
    managed_raw_cache_bytes: int
    managed_decoded_index_bytes: int
    managed_decoded_segment_bytes: int
    managed_resolved_alias_bytes: int
    managed_event_buffer_bytes: int
    managed_other_bytes: int
    managed_total_current_bytes: int
    managed_total_peak_bytes: int
    managed_total_max_bytes: int

    @classmethod
    def from_session(
        cls,
        *,
        raw_cache_bytes: int,
        decoded_index_objects: list[Any],
        decoded_segment_objects: list[Any],
        resolved_aliases: dict[str, str],
        event_buffer: list[Any],
        other_objects: list[Any],
        previous_peak: int,
        max_bytes: int,
    ) -> "ManagedMemoryAccounting":
        decoded_index_bytes = recursive_size(decoded_index_objects)
        decoded_segment_bytes = recursive_size(decoded_segment_objects)
        resolved_alias_bytes = recursive_size(resolved_aliases)
        event_buffer_bytes = recursive_size(event_buffer)
        other_bytes = recursive_size(other_objects)
        current = (
            raw_cache_bytes
            + decoded_index_bytes
            + decoded_segment_bytes
            + resolved_alias_bytes
            + event_buffer_bytes
            + other_bytes
        )
        return cls(
            managed_raw_cache_bytes=raw_cache_bytes,
            managed_decoded_index_bytes=decoded_index_bytes,
            managed_decoded_segment_bytes=decoded_segment_bytes,
            managed_resolved_alias_bytes=resolved_alias_bytes,
            managed_event_buffer_bytes=event_buffer_bytes,
            managed_other_bytes=other_bytes,
            managed_total_current_bytes=current,
            managed_total_peak_bytes=max(previous_peak, current),
            managed_total_max_bytes=max_bytes,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "managed_raw_cache_bytes": self.managed_raw_cache_bytes,
            "managed_decoded_index_bytes": self.managed_decoded_index_bytes,
            "managed_decoded_segment_bytes": self.managed_decoded_segment_bytes,
            "managed_resolved_alias_bytes": self.managed_resolved_alias_bytes,
            "managed_event_buffer_bytes": self.managed_event_buffer_bytes,
            "managed_other_bytes": self.managed_other_bytes,
            "managed_total_current_bytes": self.managed_total_current_bytes,
            "managed_total_peak_bytes": self.managed_total_peak_bytes,
            "managed_total_max_bytes": self.managed_total_max_bytes,
        }


def recursive_size(value: Any, seen: set[int] | None = None) -> int:
    if seen is None:
        seen = set()
    obj_id = id(value)
    if obj_id in seen:
        return 0
    seen.add(obj_id)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(recursive_size(key, seen) + recursive_size(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(recursive_size(item, seen) for item in value)
    elif hasattr(value, "__dict__"):
        size += recursive_size(vars(value), seen)
    return size
