from collections import OrderedDict, defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class CacheEntry:
    data: bytes
    artifact_type: str
    size_bytes: int
    pinned: bool = False


class PinnedCacheBudgetError(ValueError):
    pass


class ByteBoundedLRUCache:
    def __init__(self, max_bytes: int, max_items: int) -> None:
        if max_bytes < 0:
            raise ValueError(f"max_bytes must be non-negative: {max_bytes}")
        if max_items < 0:
            raise ValueError(f"max_items must be non-negative: {max_items}")
        self.max_bytes = max_bytes
        self.max_items = max_items
        self._items: OrderedDict[str, CacheEntry] = OrderedDict()
        self.current_bytes = 0
        self.peak_bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.oversized_rejections = 0
        self.bypass_oversized_items = 0
        self.bytes_by_artifact_type: dict[str, int] = defaultdict(int)
        self.evictions_by_artifact_type: dict[str, int] = defaultdict(int)

    def get(self, key: str) -> bytes | None:
        entry = self._items.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        self._items.move_to_end(key)
        return entry.data

    def put(self, key: str, data: bytes, *, artifact_type: str, pinned: bool = False) -> bool:
        size = len(data)
        if self.max_bytes == 0 or self.max_items == 0 or size > self.max_bytes:
            self.oversized_rejections += 1
            self.bypass_oversized_items += 1
            if pinned:
                raise PinnedCacheBudgetError("Pinned cache entry exceeds the configured byte or item budget")
            return False
        existing = self._items.pop(key, None)
        if existing is not None:
            self._subtract(existing)
        entry = CacheEntry(data=data, artifact_type=artifact_type, size_bytes=size, pinned=pinned)
        self._items[key] = entry
        self._add(entry)
        if not self._evict_to_budget(protect_key=key if pinned else None):
            self._items.pop(key, None)
            self._subtract(entry)
            self.oversized_rejections += 1
            if pinned:
                raise PinnedCacheBudgetError("Pinned cache entries exceed the configured byte or item budget")
            return False
        self.peak_bytes = max(self.peak_bytes, self.current_bytes)
        return True

    def clear(self, *, reset_stats: bool = False) -> None:
        self._items.clear()
        self.current_bytes = 0
        self.bytes_by_artifact_type.clear()
        if reset_stats:
            self.peak_bytes = 0
            self.hits = 0
            self.misses = 0
            self.evictions = 0
            self.oversized_rejections = 0
            self.bypass_oversized_items = 0
            self.evictions_by_artifact_type.clear()

    @property
    def item_count(self) -> int:
        return len(self._items)

    def metrics(self) -> dict[str, object]:
        return {
            "cache_current_bytes": self.current_bytes,
            "cache_peak_bytes": self.peak_bytes,
            "cache_max_bytes": self.max_bytes,
            "cache_items": self.item_count,
            "cache_hits": self.hits,
            "cache_misses": self.misses,
            "cache_evictions": self.evictions,
            "cache_oversized_rejections": self.oversized_rejections,
            "cache_bypass_oversized_item": self.bypass_oversized_items,
            "cache_bytes_by_artifact_type": dict(self.bytes_by_artifact_type),
            "cache_evictions_by_artifact_type": dict(self.evictions_by_artifact_type),
        }

    def _evict_to_budget(self, *, protect_key: str | None) -> bool:
        while self.current_bytes > self.max_bytes or len(self._items) > self.max_items:
            victim_key = self._first_evictable_key(protect_key)
            if victim_key is None:
                return False
            victim = self._items.pop(victim_key)
            self._subtract(victim)
            self.evictions += 1
            self.evictions_by_artifact_type[victim.artifact_type] += 1
        return True

    def _first_evictable_key(self, protect_key: str | None) -> str | None:
        for key, entry in self._items.items():
            if key == protect_key:
                continue
            if not entry.pinned:
                return key
        return None

    def _add(self, entry: CacheEntry) -> None:
        self.current_bytes += entry.size_bytes
        self.bytes_by_artifact_type[entry.artifact_type] += entry.size_bytes

    def _subtract(self, entry: CacheEntry) -> None:
        self.current_bytes -= entry.size_bytes
        self.bytes_by_artifact_type[entry.artifact_type] -= entry.size_bytes
        if self.bytes_by_artifact_type[entry.artifact_type] <= 0:
            self.bytes_by_artifact_type.pop(entry.artifact_type, None)
