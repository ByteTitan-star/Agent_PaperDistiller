# 项目A2A协议代码实现分析

经过深入分析代码，我发现项目中**并没有使用A2A协议包或标准A2A实现**。相反，项目采用的是**自研的基于事件总线的A2A架构**。以下是具体的代码实现分析：

## A2A协议的误解澄清

项目中并不存在以下情况：
- ❌ 没有导入特定的"A2A"包
- ❌ 没有使用标准化的A2A协议实现
- ❌ 没有外部的A2A框架依赖

## 真正的A2A实现方式：自研事件总线架构

### 1. 核心事件总线（EventBus）

```python
# backend/app/harness/events.py - 这是项目的"A2A协议"核心实现

class EventBus:
    """进程内同步事件分发器 - 这就是项目的A2A通信机制"""
    
    def __init__(self) -> None:
        # 按模式订阅的回调列表：[(pattern, callback), ...]
        self._subscribers: list[tuple[str, HookCallback]] = []
        # 全局订阅的回调列表：接收所有事件
        self._global_subscribers: list[HookCallback] = []
    
    def subscribe(self, event_pattern: str, callback: HookCallback) -> None:
        """注册模式匹配订阅"""
        self._subscribers.append((event_pattern, callback))
    
    def emit(self, event: HarnessEvent) -> None:
        """发射事件，通知所有匹配的订阅者"""
        # 遍历所有模式订阅者，匹配则调用回调
        for pattern, callback in self._subscribers:
            if fnmatch.fnmatch(event_key, pattern) or fnmatch.fnmatch(event.component, pattern):
                try:
                    callback(event)
                except Exception:
                    pass  # 回调异常不影响其他订阅者
```

### 2. Agent间通信机制

```python
# backend/app/harness/agents/base.py - Agent间通信的基础实现

class BaseAgent(ABC):
    """所有Agent的基类，通过事件总线进行A2A通信"""
    
    async def execute(self, prompt: str, **kwargs: object) -> AgentResult:
        """模板方法 — 执行Agent的完整生命周期，通过事件总线通信"""
        # 阶段1：初始化，通过事件总线广播
        self.on_init()
        self.event_bus.emit(
            HarnessEvent(layer="agent", component=self.name, action="init"),
        )
        try:
            # 阶段2：预处理，通过事件总线广播
            prepared = self.on_pre_run(prompt, **kwargs)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="pre_run"),
            )
            # 阶段3：执行具体逻辑（由子类实现）
            result = await self._do_run(prepared, **kwargs)
            # 阶段4：后处理，通过事件总线广播
            self.on_post_run(result)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="post_run",
                            payload={"has_error": result.error is not None}),
            )
            return result
        except Exception as exc:
            # 异常处理：调用错误钩子，通过事件总线广播错误
            self.on_error(exc)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="error",
                            payload={"error": str(exc)}),
            )
            return AgentResult(error=str(exc))
```

### 3. Agent协作模式

```python
# backend/app/harness/collaboration/debate.py - 辩论模式的A2A协作

class DebatePattern(BaseCollaborationPattern):
    """双Agent辩论模式：一个提议，一个评论，最后裁决"""
    
    async def run(self, input_text: str, **kwargs: object) -> CollaborationResult:
        """执行辩论流程，通过事件总线通信"""
        for round_idx in range(self.rounds):
            # 阶段1：提议者生成方案，通过事件总线通信
            proposal = await proposer.execute(input_text, **kwargs)
            
            # 阶段2：评论者评审方案，通过事件总线通信
            review = await critic.execute(str(proposal.content), **kwargs)
            
        # 阶段3：裁决
        final = self._adjudicate(all_proposals, all_reviews)
```

### 4. ToT协作实现

```python
# backend/app/harness/agents/tot_agent.py - ToT模式的A2A协作

class ToTAgent(BaseAgent):
    """Tree-of-Thoughts Agent，协调生成和评估两个子Agent"""
    
    def __init__(self, generator: DeepSeekAgent, evaluator: QwenAgent, ...):
        self.generator = generator  # DeepSeek Agent
        self.evaluator = evaluator    # Qwen Agent
    
    async def _do_run(self, prompt: str, **kwargs) -> AgentResult:
        # 阶段1：使用DeepSeek生成候选分支，通过事件总线通信
        candidates, generation_errors = await self._generate_branches(title, tags, evidence)
        
        # 阶段2：使用Qwen评估并打分，通过事件总线通信
        reviewer_scores, overall_comment = await self._evaluate_branches(candidates)
        
        # 阶段3：扩展、剪枝、选出最优，通过事件总线通信
        winner = self._expand_and_prune(candidates, reviewer_scores, overall_comment)
        
        return AgentResult(content=[winner], metadata={"collaboration_mode": "ToT"})
```

### 5. 应用层A2A协调

```python
# backend/app/harness/app.py - 顶层A2A协调器

class AppHarness:
    """中央生命周期管理器，A2A架构的核心协调器"""
    
    def __init__(self) -> None:
        # 初始化事件总线 - 这是整个A2A架构的核心
        self.event_bus = EventBus()
        
    async def startup(self) -> None:
        """按依赖顺序初始化所有A2A组件"""
        # Agent工厂
        self.agent_factory = AgentFactory(self.event_bus, self.settings)
        
        # 流水线编排器
        self.pipeline_harness = PipelineHarness(
            storage=self.storage,
            broker=self.broker,
            settings=self.settings,
            event_bus=self.event_bus,  # 通过事件总线连接
            hitl_manager=self.hitl_manager,
        )
        
        # 多Agent协作注册
        self.collaboration_registry = CollaborationRegistry(self.event_bus)
```

## A2A通信的数据结构

### 1. 事件类型定义

```python
# backend/app/harness/_types.py - A2A通信的数据结构

class HarnessEvent:
    """harness内部事件，用于Agent间A2A通信"""
    
    def __init__(
        self,
        layer: str,              # 事件层：agent/pipeline/tool/session/hitl/collaboration
        component: str,         # 组件名称
        action: str,            # 动作名称
        payload: dict[str, Any] | None = None,  # 附加数据
    ):
        # 自动填充UTC时间戳
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
```

### 2. Agent间通信示例

```python
# Agent间A2A通信的完整流程示例

# 1. DeepSeek Agent执行并广播事件
generator_agent = DeepSeekAgent(event_bus, settings)
result = await generator_agent.execute(prompt, system_prompt="专家提示", temperature=0.8)

# 2. Qwen Agent监听并响应
evaluator_agent = QwenAgent(event_bus, settings)
# 事件总线会自动将DeepSeek的执行结果传递给订阅者

# 3. 协作模式通过事件总线协调
collaboration = DebatePattern(generator_agent, evaluator_agent, event_bus)
result = await collaboration.run(input_text, **kwargs)
```

## 项目的A2A架构特点

### 1. 发布-订阅模式
- Agent通过`event_bus.emit()`发布事件
- 其他Agent通过`event_bus.subscribe()`订阅事件
- 支持模式匹配（如`"agent.*"`订阅所有agent事件）

### 2. 事件驱动架构
- Agent状态变化通过事件广播
- 组件解耦，不直接依赖
- 便于调试和追踪

### 3. 进程内通信
- 同步事件调用
- 不涉及网络通信
- 性能高，延迟低

## 总结

项目中**没有使用外部A2A协议包**，而是实现了一套**自研的基于事件总线的A2A架构**：

1. **EventBus** - 核心A2A通信机制
2. **HarnessEvent** - A2A通信的数据结构
3. **BaseAgent** - Agent间通信的基础框架
4. **协作模式** - 高级A2A协作实现

这种自研的A2A架构虽然没有使用标准协议，但实现了Agent间的有效通信和协作，特别适合多Agent系统的内部通信需求。