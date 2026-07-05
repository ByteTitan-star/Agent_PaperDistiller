"""进程内同步事件总线（EventBus）。

提供发布-订阅模式的事件分发机制：
- 各组件在关键节点通过 emit() 发布事件
- 订阅者通过 subscribe() / subscribe_all() 注册回调
- 事件匹配支持 glob 通配符（如 "agent.*" 匹配 "agent.init"）

注意：这是进程内同步调用，不是 SSE（Server-Sent Events），
也不是跨网络的消息队列，仅用于同一个 Python 进程内的模块间通信。
"""

from __future__ import annotations

import fnmatch
from datetime import UTC

from ._types import HarnessEvent, HookCallback


class EventBus:
    """进程内同步事件分发器。

    各层（agent / pipeline / tool / session / hitl / collaboration）
    在关键节点通过 emit() 发射事件，订阅了对应模式的回调函数
    会被同步调用。

    事件匹配规则：
    - subscribe(event_pattern, callback)：event_pattern 支持 glob 通配符
      匹配 "{layer}.{action}" 或 "{component}" 格式
    - subscribe_all(callback)：接收所有事件，不做过滤

    使用示例：
        bus = EventBus()

        # 订阅所有 agent 层的事件
        bus.subscribe("agent.*", lambda e: print(f"Agent event: {e.action}"))

        # 订阅特定组件的事件
        bus.subscribe("DeepSeekAgent", lambda e: print(f"DeepSeek: {e.action}"))

        # 订阅所有事件（用于全局日志）
        bus.subscribe_all(lambda e: logger.info(e))
    """

    def __init__(self) -> None:
        # 按模式订阅的回调列表：[(pattern, callback), ...]
        self._subscribers: list[tuple[str, HookCallback]] = []
        # 全局订阅的回调列表：接收所有事件
        self._global_subscribers: list[HookCallback] = []

    def subscribe(self, event_pattern: str, callback: HookCallback) -> None:
        """注册模式匹配订阅。

        Args:
            event_pattern: glob 风格的事件匹配模式。
                匹配目标为 "{layer}.{action}"（如 "agent.init"）
                或 "{component}"（如 "DeepSeekAgent"）。
                支持通配符 "*"，如 "agent.*" 匹配所有 agent 层事件。
            callback: 事件回调函数，接收 HarnessEvent 参数，无返回值。
        """
        self._subscribers.append((event_pattern, callback))

    def subscribe_all(self, callback: HookCallback) -> None:
        """注册全局订阅，接收所有事件，不做任何过滤。

        Args:
            callback: 事件回调函数，接收 HarnessEvent 参数，无返回值。
        """
        self._global_subscribers.append(callback)

    def emit(self, event: HarnessEvent) -> None:
        """发射事件，通知所有匹配的订阅者。

        自动为事件填充 UTC 时间戳（如果未设置）。
        事件匹配 key 为 "{layer}.{action}"，同时也会与 component 做匹配。
        回调中的异常会被静默吞掉，不影响其他订阅者。

        Args:
            event: 要发射的事件对象。
        """
        # 如果事件没有时间戳，自动填充当前 UTC 时间
        if not event.timestamp:
            from datetime import datetime

            event.timestamp = datetime.now(UTC).isoformat()

        # 构建事件匹配 key，格式为 "{layer}.{action}"
        event_key = f"{event.layer}.{event.action}"

        # 遍历所有模式订阅者，匹配则调用回调
        for pattern, callback in self._subscribers:
            if fnmatch.fnmatch(event_key, pattern) or fnmatch.fnmatch(event.component, pattern):
                try:
                    callback(event)
                except Exception:
                    pass  # 回调异常不影响其他订阅者

        # 通知全局订阅者
        for callback in self._global_subscribers:
            try:
                callback(event)
            except Exception:
                pass  # 同上，静默吞掉异常

    def clear(self) -> None:
        """清除所有订阅者（包括模式订阅和全局订阅）。

        通常在测试或重置时使用。
        """
        self._subscribers.clear()
        self._global_subscribers.clear()
