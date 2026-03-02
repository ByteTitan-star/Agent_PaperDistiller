<template>
  <aside class="guide-panel">
    <div class="guide-head">
      <div class="guide-title">使用提示</div>
      <div class="guide-subtitle">按以下流程可获得更稳定结果</div>
    </div>

    <el-card shadow="never" class="model-card">
      <template #header>
        <div class="card-header">当前模型信息</div>
      </template>
      <p><strong>生成模型：</strong>{{ info.generation_model_name || info.llm_model_name }}</p>
      <p><strong>评估模型：</strong>{{ info.evaluation_model_name || "Qwen3" }}</p>
      <p><strong>向量模型：</strong>{{ info.embedding_model_name }}</p>
      <p><strong>模型提供方：</strong>{{ info.model_provider }}</p>
      <p><strong>流水线模式：</strong>{{ info.pipeline_mode }}</p>
      <p><strong>协同模式：</strong>{{ info.collaboration_mode }}</p>
      <el-tag type="success" effect="plain">异构协同 / Multi-Agent Mode</el-tag>
    </el-card>

    <el-card shadow="never" class="steps-card">
      <template #header>
        <div class="card-header">推荐操作步骤</div>
      </template>
      <ol class="steps">
        <li>上传结构完整的论文 PDF（优先可复制文本版）。</li>
        <li>先选择目标语言，再选择合适总结模板。</li>
        <li>等待进度到 100%，再进入阅读工作台查看结果。</li>
        <li>先阅读“核心摘要”，再对“改进建议”做二次提问。</li>
        <li>在问答框里给出明确问题，如“方法局限是什么”。</li>
      </ol>
    </el-card>
  </aside>
</template>

<script setup>
defineProps({
  info: {
    type: Object,
    required: true
  }
});
</script>

<style scoped>
.guide-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.guide-head {
  padding: 2px 4px;
}

.guide-title {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
}

.guide-subtitle {
  margin-top: 4px;
  color: #475569;
  font-size: 13px;
}

.model-card,
.steps-card {
  border-radius: 12px;
  border: 1px solid #d9e2f1;
  background: #f8fbff;
}

.card-header {
  font-weight: 600;
  color: #1e293b;
}

.model-card p {
  margin: 8px 0;
  color: #1f2937;
  font-size: 13px;
}

.steps {
  margin: 0;
  padding-left: 18px;
  color: #334155;
  line-height: 1.8;
  font-size: 13px;
}
</style>
