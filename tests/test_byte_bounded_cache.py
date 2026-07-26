from cida.infrastructure.byte_bounded_cache import ByteBoundedLRUCache
import pytest


def test_byte_bounded_cache_rejects_negative_budgets():
    with pytest.raises(ValueError, match="max_bytes"):
        ByteBoundedLRUCache(max_bytes=-1, max_items=1)
    with pytest.raises(ValueError, match="max_items"):
        ByteBoundedLRUCache(max_bytes=1, max_items=-1)


def test_byte_bounded_cache_evicts_lru_to_respect_byte_budget():
    cache = ByteBoundedLRUCache(max_bytes=6, max_items=10)

    assert cache.put("a", b"aaa", artifact_type="sidecar") is True
    assert cache.put("b", b"bbb", artifact_type="sidecar") is True
    assert cache.get("a") == b"aaa"
    assert cache.put("c", b"ccc", artifact_type="segment") is True

    assert cache.current_bytes <= 6
    assert cache.get("b") is None
    assert cache.get("a") == b"aaa"
    assert cache.get("c") == b"ccc"
    assert cache.evictions == 1


def test_byte_bounded_cache_rejects_oversized_items_without_exceeding_budget():
    cache = ByteBoundedLRUCache(max_bytes=4, max_items=2)

    assert cache.put("big", b"12345", artifact_type="content") is False

    assert cache.current_bytes == 0
    assert cache.oversized_rejections == 1
    assert cache.bypass_oversized_items == 1


def test_byte_bounded_cache_clear_can_reset_stats():
    cache = ByteBoundedLRUCache(max_bytes=4, max_items=2)
    cache.put("a", b"aa", artifact_type="manifest")
    cache.get("missing")

    cache.clear(reset_stats=True)

    assert cache.current_bytes == 0
    assert cache.item_count == 0
    assert cache.hits == 0
    assert cache.misses == 0


def test_byte_bounded_cache_updates_existing_entry_and_handles_pinned_pressure():
    cache = ByteBoundedLRUCache(max_bytes=4, max_items=2)

    assert cache.put("a", b"aa", artifact_type="manifest") is True
    assert cache.put("a", b"a", artifact_type="manifest") is True
    assert cache.put("pinned", b"bbb", artifact_type="alias_index", pinned=True) is True
    assert cache.put("second-pinned", b"bb", artifact_type="alias_index", pinned=True) is True

    assert cache.current_bytes <= 4
    assert cache.get("a") is None
    assert cache.get("second-pinned") == b"bb"
    assert cache.bytes_by_artifact_type == {"alias_index": 2}
