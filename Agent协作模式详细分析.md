# 项目Agent协作模式详细分析

您的分析非常准确！AgentPaperDistiller项目确实在两个核心层次上实现了多Agent协作机制：

## 一、LangGraph状态图层面的协作模式

### 1. 状态图架构

项目使用LangGraph构建了一个有向无环图（DAG）作为顶层协作框架：

```python
# 状态图节点（backend/app/pipeline/workflow_graph.py）

class PaperState(TypedDict, total=False):
    """论文处理状态图 - 在各节点之间传递数据的"托盘"结构"""
    
    task_id: str        # 任务唯一标识
    paper_id: str       # 论文唯一标识
    title: str          # 论文标题
    sections: list[tuple[str, str]]  # 章节列表
    chunks: list[str]   # 文本块列表
    tags: list[str]     # 领域标签
    # ... 其他状态字段
```

### 2. 节点协作流程

```python
# 节点执行顺序和依赖关系
workflow = StateGraph(PaperState)

# 1. 解析节点：提取PDF文本和结构
async def parse_node(state: PaperState) -> PaperState:
    # 解析PDF，生成text, sections, chunks
    return {"text": text, "sections": sections, "chunks": chunks}

# 2. 翻译节点：全文翻译
async def translate_node(state: PaperState) -> PaperState:
    # 翻译所有章节，返回translated_sections
    return {"translated_sections": translated_sections}

# 3. 摘要节点：提取核心信息
async def summarize_node(state: PaperState) -> PaperState:
    # 使用LLM按模板提取摘要，推断领域标签
    return {"tags": tags, "template_text": template_text}

# 4. 创新改进节点：生成改进建议
async def critique_node(state: PaperState) -> PaperState:
    # 生成创新改进方案（包含ToT多Agent协作）
    return {}  # 空状态，结果已保存到存储
```

### 3. 数据托盘机制

关键特点：**前一个节点的输出会作为后一个节点的输入**

```python
# 节点间数据传递示例
parse_node → translate_node:
    输入: state包含原始PDF
    输出: state包含解析后的text, sections, chunks
    数据流向: text, sections, chunks被翻译节点使用

translate_node → summarize_node:
    输入: state包含translated_sections
    输出: state包含tags, template_text
    数据流向: 翻译结果用于摘要提取

summarize_node → critique_node:
    输入: state包含tags, chunks, translated_chunks
    输出: 空状态（结果保存到存储）
    数据流向: 标签和文本块用于创新改进生成
```

### 4. 条件分支路由

```python
def route_after_translate(state: PaperState) -> str:
    """翻译后路由判断"""
    failures = int(state.get("translation_failures", 0))
    retry_count = int(state.get("translation_retry_count", 0))
    
    if failures > 0 and retry_count <= settings.pipeline_translation_retry_limit:
        return "translate"  # 重试翻译
    return "summarize"     # 进入摘要阶段
```

## 二、创新改进节点内部的Agent辩论模式

### 1. ToT（Tree-of-Thought）协作机制

在`critique_node`中，项目实现了更复杂的ToT多Agent协作：

```python
# ToT协作流程（backend/app/pipeline/tot_generator.py）

def generate_tot_idea(
    title: str,
    tags: list[str],
    evidence: list[str],
    settings: Settings,
    user_id: int | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    三阶段ToT协作流程：
    1. 生成阶段：使用DeepSeek生成多个候选方案
    2. 评估阶段：使用Qwen对候选方案进行多维度评分
    3. ToT阶段：计算综合得分，排序选择最优方案
    """
```

### 2. 辩论模式的具体实现

确实，这是一个**生成者 vs 评审者**的辩论模式：

#### 阶段1：生成者（DeepSeek Agent） - 提出方案
```python
# 生成者角色：攻击策略设计专家
generation_prompt = (
    "你是攻击策略设计者。请输出一个独立的创新方案，且与常见方案显著不同。\n"
    "只输出 JSON，不要解释。\n\n"
    f"分支编号: {idx + 1}\n"
    f"论文标题: {title}\n"
    f"领域标签: {', '.join(tags)}\n"
    f"证据片段: {' | '.join(evidence[:3])}"
)

# 调用DeepSeek模型生成候选方案
response = generation_client.chat.completions.create(
    model=settings.deepseek_model,
    messages=[...],
    temperature=settings.tot_generation_temperature,
    max_tokens=900,
)
```

#### 阶段2：评审者（Qwen Agent） - 评估打分
```python
# 评审者角色：严谨的顶会评审
reviewer_prompt = (
    "你是严谨的顶会评审（Reviewer）。"
    "请对每个候选方案在以下维度打分（1-10）："
    "asr_gain(越高越好)、implementation_cost(越低越好)、stealthiness(越高越好)。"
    "仅输出 JSON。\n\n"
    f"候选方案: {json.dumps(candidates, ensure_ascii=False)}"
)

# 调用Qwen模型进行评审
review_response = evaluation_client.chat.completions.create(
    model=settings.qwen_model,
    messages=[...],
    temperature=settings.tot_reviewer_temperature,
    max_tokens=900,
)
```

#### 阶段3：ToT裁决 - 剪枝选择
```python
# 综合评分公式：Score = α*ASR_Gain - β*Implementation_Cost + γ*Stealthiness
alpha = float(settings.tot_score_alpha)
beta = float(settings.tot_score_beta)
gamma = float(settings.tot_score_gamma)

# 计算每个候选的最终得分并排序
for candidate in candidates:
    score = alpha * asr_gain - beta * implementation_cost + gamma * stealthiness
    candidate["final_score"] = round(score, 3)

# 选择最优分支
ranked_candidates = sorted(candidates, key=lambda x: x.get("final_score", 0), reverse=True)
winner = ranked_candidates[0]
```

### 3. 辩论模式的数据流

```
辩论过程：
生成者 → [候选方案1] → 评审者 → [评分结果1]
        [候选方案2] → 评审者 → [评分结果2]
        [候选方案3] → 评审者 → [评分结果3]
                      ↓
                ToT裁决 → [最优方案]
```

### 4. 执行轨迹记录

系统完整记录了辩论过程中的所有步骤：

```python
winner["execution_order"] = (
    f"先生成({generation_agent}) → 后评估({evaluation_agent}) → 再 ToT 分支扩展与剪枝"
)
winner["model_steps"] = [
    winner.get("generation_step", ""),
    winner.get("evaluation_step", ""),
    winner.get("tot_step", ""),
]
winner["selection_reason"] = (
    f"Score = {alpha}*ASR_Gain - {beta}*Implementation_Cost + {gamma}*Stealthiness; "
    f"winner score={winner.get('final_score')}"
)
```

## 三、协作模式的总结

### 1. 双层协作架构

```
第一层：LangGraph状态图协作
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   解析节点   │ → │   翻译节点   │ → │   摘要节点   │ → │ 创新改进节点 │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
         │                │                │                │
         ▼                ▼                ▼                ▼
   [PDF文本提取]   [全文翻译]   [摘要提取]   [ToT多Agent辩论]

第二层：ToT内部Agent协作
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ DeepSeek    │ → │   Qwen      │ → │   ToT裁决   │
│  生成者     │    │   评审者    │    │   选择器    │
└─────────────┘    └─────────────┘    └─────────────┘
         │                │                │
         ▼                ▼                ▼
   [候选方案]   [多维度评分]   [最优方案]
```

### 2. 协作模式的特点

1. **数据托盘机制**：节点间通过共享状态传递数据
2. **辩论模式**：生成者与评审者的对抗性协作
3. **评分机制**：多维度评估和综合评分
4. **选择策略**：基于评分的优胜劣汰
5. **轨迹记录**：完整记录协作过程和决策理由

### 3. 多Agent协作的优势

1. **专业化分工**：不同Agent专注于不同任务（生成、评估、裁决）
2. **质量保证**：通过评审者评估确保输出质量
3. **多样性保证**：生成多个候选方案，避免单一路径
4. **可解释性**：记录完整的协作过程和决策依据

这种双层协作架构既保证了整体流程的结构化，又保证了关键节点的深度协作，是一种非常优秀的多Agent系统设计。