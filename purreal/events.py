from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any

from .types import EventContext, EventHandler, PoolEvent

logger = logging.getLogger(__name__)


class EventBus:
	__slots__ = ("_handlers",)

	def __init__(self) -> None:
		self._handlers: dict[PoolEvent, list[EventHandler]] = defaultdict(list)

	def on(self, event: PoolEvent, handler: EventHandler) -> None:
		self._handlers[event].append(handler)

	def off(self, event: PoolEvent, handler: EventHandler) -> None:
		try:
			self._handlers[event].remove(handler)
		except ValueError:
			pass

	async def emit(self, ctx: EventContext) -> None:
		handlers = self._handlers.get(ctx.event)
		if not handlers:
			return
		for handler in handlers:
			try:
				if inspect.iscoroutinefunction(handler):
					asyncio.create_task(handler(ctx))
				else:
					handler(ctx)
			except Exception as e:
				logger.warning(f"Event handler error for {ctx.event.name}: {e}")

	def clear(self) -> None:
		self._handlers.clear()
