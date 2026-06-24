# -*- coding: utf-8 -*-
"""Test suite for purreal event bus."""

import asyncio
import time

import pytest

from purreal.events import EventBus
from purreal.types import EventContext, PoolEvent


class TestEventBus:
    @pytest.mark.asyncio
    async def test_sync_handler(self):
        bus = EventBus()
        received = []

        def handler(ctx):
            received.append(ctx)

        bus.on(PoolEvent.ACQUIRE, handler)
        ctx = EventContext(event=PoolEvent.ACQUIRE, timestamp=time.monotonic())
        await bus.emit(ctx)

        assert len(received) == 1
        assert received[0] is ctx

    @pytest.mark.asyncio
    async def test_async_handler(self):
        bus = EventBus()
        received = []

        async def handler(ctx):
            received.append(ctx)

        bus.on(PoolEvent.CREATE, handler)
        ctx = EventContext(event=PoolEvent.CREATE, timestamp=time.monotonic())
        await bus.emit(ctx)
        await asyncio.sleep(0.01)  # Let task run

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_multiple_handlers(self):
        bus = EventBus()
        count = [0]

        def h1(ctx):
            count[0] += 1

        def h2(ctx):
            count[0] += 10

        bus.on(PoolEvent.RELEASE, h1)
        bus.on(PoolEvent.RELEASE, h2)
        await bus.emit(
            EventContext(event=PoolEvent.RELEASE, timestamp=time.monotonic())
        )

        assert count[0] == 11

    @pytest.mark.asyncio
    async def test_off_removes_handler(self):
        bus = EventBus()
        count = [0]

        def handler(ctx):
            count[0] += 1

        bus.on(PoolEvent.DESTROY, handler)
        bus.off(PoolEvent.DESTROY, handler)
        await bus.emit(
            EventContext(event=PoolEvent.DESTROY, timestamp=time.monotonic())
        )

        assert count[0] == 0

    @pytest.mark.asyncio
    async def test_handler_error_isolated(self):
        bus = EventBus()
        count = [0]

        def bad_handler(ctx):
            raise ValueError("boom")

        def good_handler(ctx):
            count[0] += 1

        bus.on(PoolEvent.TIMEOUT, bad_handler)
        bus.on(PoolEvent.TIMEOUT, good_handler)
        await bus.emit(
            EventContext(event=PoolEvent.TIMEOUT, timestamp=time.monotonic())
        )

        assert count[0] == 1  # Good handler still ran

    @pytest.mark.asyncio
    async def test_emit_nonexistent_event(self):
        bus = EventBus()
        # Should not raise
        await bus.emit(
            EventContext(event=PoolEvent.TELEPORT, timestamp=time.monotonic())
        )

    @pytest.mark.asyncio
    async def test_clear(self):
        bus = EventBus()
        count = [0]
        bus.on(PoolEvent.ACQUIRE, lambda ctx: count.__setitem__(0, count[0] + 1))
        bus.clear()
        await bus.emit(
            EventContext(event=PoolEvent.ACQUIRE, timestamp=time.monotonic())
        )
        assert count[0] == 0
