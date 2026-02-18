"""Tests for BoundedSet — FIFO eviction set."""

from app.bounded_set import BoundedSet
import pytest


class TestBoundedSetBasic:
    """Basic set operations."""

    def test_add_and_contains(self):
        s = BoundedSet(maxlen=10)
        s.add("a")
        assert "a" in s
        assert "b" not in s

    def test_len(self):
        s = BoundedSet(maxlen=10)
        assert len(s) == 0
        s.add("a")
        s.add("b")
        assert len(s) == 2

    def test_duplicate_add_is_noop(self):
        s = BoundedSet(maxlen=10)
        s.add("a")
        s.add("a")
        assert len(s) == 1

    def test_clear(self):
        s = BoundedSet(maxlen=10)
        s.add("a")
        s.add("b")
        s.clear()
        assert len(s) == 0
        assert "a" not in s

    def test_repr(self):
        s = BoundedSet(maxlen=5)
        s.add("x")
        assert "maxlen=5" in repr(s)
        assert "size=1" in repr(s)

    def test_invalid_maxlen_raises(self):
        with pytest.raises(ValueError):
            BoundedSet(maxlen=0)
        with pytest.raises(ValueError):
            BoundedSet(maxlen=-1)


class TestBoundedSetEviction:
    """FIFO eviction behavior."""

    def test_evicts_oldest_when_full(self):
        s = BoundedSet(maxlen=3)
        s.add("a")
        s.add("b")
        s.add("c")
        assert len(s) == 3

        # Adding a 4th should evict "a" (oldest)
        s.add("d")
        assert len(s) == 3
        assert "a" not in s
        assert "b" in s
        assert "c" in s
        assert "d" in s

    def test_evicts_in_fifo_order(self):
        s = BoundedSet(maxlen=2)
        s.add("first")
        s.add("second")

        s.add("third")  # evicts "first"
        assert "first" not in s
        assert "second" in s
        assert "third" in s

        s.add("fourth")  # evicts "second"
        assert "second" not in s
        assert "third" in s
        assert "fourth" in s

    def test_maxlen_one(self):
        s = BoundedSet(maxlen=1)
        s.add("a")
        assert "a" in s
        s.add("b")
        assert "a" not in s
        assert "b" in s
        assert len(s) == 1

    def test_duplicate_does_not_trigger_eviction(self):
        s = BoundedSet(maxlen=3)
        s.add("a")
        s.add("b")
        s.add("c")
        # Re-adding existing item should not evict anything
        s.add("b")
        assert len(s) == 3
        assert "a" in s
        assert "b" in s
        assert "c" in s

    def test_large_set_eviction(self):
        """Verify eviction works over many insertions."""
        s = BoundedSet(maxlen=100)
        for i in range(500):
            s.add(f"item-{i}")

        assert len(s) == 100
        # Only the last 100 should remain
        for i in range(400):
            assert f"item-{i}" not in s
        for i in range(400, 500):
            assert f"item-{i}" in s
