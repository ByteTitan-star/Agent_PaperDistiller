<template>
  <main class="workspace-page">
    <header class="workspace-head">
      <div class="head-left">
        <el-button @click="$router.push('/dashboard')">返回总览</el-button>
        <h1 class="page-title">{{ paper?.title || '阅读工作台' }}</h1>
      </div>
      <div class="head-right">
        <el-tag :type="statusTypeMap[paper?.status] || 'info'">{{ statusLabel(paper?.status) }}</el-tag>
        <el-tag class="model-tag" effect="dark">{{ modelListLabel }}</el-tag>
        <el-tag class="multi-agent-tag" type="success" effect="plain">Multi-Agent Mode</el-tag>
      </div>
    </header>

    <section class="workspace-grid">
      <article class="panel pdf-panel">
        <div class="panel-head">
          <h2>原文 PDF</h2>
          <div class="panel-actions">
            <el-button size="small" @click="openPdfInNewTab">新窗口打开</el-button>
            <el-button size="small" type="primary" @click="downloadPdf">下载 PDF</el-button>
          </div>
        </div>
        <iframe
          v-if="paper"
          :key="pdfInlineUrl"
          :src="pdfInlineUrl"
          class="pdf-frame"
          title="论文 PDF 阅读器"
        />
        <el-empty v-else description="未找到该论文" />
      </article>

      <article class="panel content-panel">
        <div class="panel-head">
          <h2>阅读内容</h2>
          <div class="content-head-actions">
            <el-button size="small" @click="openLayoutTranslation">双栏翻译版</el-button>
            <el-tag effect="plain">模板：{{ paper?.summary_template || 'N/A' }}</el-tag>
          </div>
        </div>

        <div class="guide-strip">
          建议阅读顺序：先看「核心摘要」→ 再看「改进与创新」→ 最后看「全文翻译」。
        </div>

        <div class="content-body">
          <el-tabs v-model="activeTab" @tab-change="loadContent">
            <el-tab-pane label="全文翻译" name="translation" />
            <el-tab-pane label="核心摘要" name="summary" />
            <el-tab-pane label="改进与创新" name="improvement" />
          </el-tabs>

          <el-skeleton class="content-skeleton" :loading="contentLoading" animated :rows="12">
            <article class="markdown-body" v-html="renderMarkdown(contents[activeTab])"></article>
          </el-skeleton>
        </div>
      </article>

      <aside class="panel chat-panel">
        <div class="panel-head">
          <h2>论文问答</h2>
          <el-tag effect="plain">Top-K: 3</el-tag>
        </div>

        <p class="chat-subtitle">可继续追问：方法局限、实验设置、可复现性、可扩展方向等。</p>

        <div class="chat-box">
          <div v-for="(item, idx) in messages" :key="idx" :class="['message', item.role]">
            <div class="message-role">{{ item.role === 'user' ? '你' : 'Agent' }}</div>
            
            <div 
              v-if="item.role === 'assistant'" 
              class="message-text chat-markdown" 
              v-html="renderChatMarkdown(item.text)"
            ></div>
            
            <pre v-else class="message-text">{{ item.text }}</pre>
          </div>
        </div>

        <div class="chat-input">
          <el-input
            v-model="question"
            type="textarea"
            :rows="3"
            placeholder="例如：这篇论文的核心创新点是否可迁移到别的数据集？"
          />
          <el-button type="primary" :loading="sending" @click="sendQuestion">发送</el-button>
        </div>
      </aside>
    </section>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import MarkdownIt from "markdown-it";

import {
  askPaper,
  getPaperContent,
  getPaperPdfDownloadUrl,
  getPaperPdfUrl,
  getTranslationLayoutUrl
} from "../api/client";
import { usePaperStore } from "../stores/papers";
import { useSystemStore } from "../stores/system";

// Markdown 渲染器：禁用原始 HTML，避免直接渲染后端返回中的 HTML 片段。
const md = new MarkdownIt({ html: false, breaks: true, linkify: true });

// 路由参数与全局状态。
const route = useRoute();
const store = usePaperStore();
const systemStore = useSystemStore();

// 页面核心状态：论文元数据、当前 tab、内容缓存。
const paper = ref(null);
const activeTab = ref("summary");
const contentLoading = ref(false);
const contents = reactive({
  translation: "",
  summary: "",
  improvement: ""
});

const question = ref("");
const sending = ref(false);
// 问答消息列表：[{ role: "user"|"assistant", text: string }]
const messages = ref([]);

// 后端论文状态到样式/文案映射。
const statusTypeMap = {
  completed: "success",
  processing: "warning",
  failed: "danger"
};

const statusTextMap = {
  completed: "已完成",
  processing: "处理中",
  failed: "处理失败"
};

const statusLabel = (status) => statusTextMap[status] || status || "未知状态";

// 由路由参数动态计算 paperId 与相关资源 URL。
const paperId = computed(() => route.params.paperId);
const pdfInlineUrl = computed(() => `${getPaperPdfUrl(paperId.value)}#view=FitH`);
const pdfDownloadUrl = computed(() => getPaperPdfDownloadUrl(paperId.value));
const generationModel = computed(() => systemStore.info.generation_model_name || "DeepSeek-V3");
const evaluationModel = computed(() => systemStore.info.evaluation_model_name || "Qwen3");
const modelListLabel = computed(
  () => `生成模型：${generationModel.value} | 评估模型：${evaluationModel.value}`
);

const renderMarkdown = (source) => md.render(source || "_暂无内容，请稍后刷新。_");

// 👇 新增这一行：专门给聊天框用的 Markdown 渲染器
const renderChatMarkdown = (source) => md.render(source || "");

const openPdfInNewTab = () => {
  // 打开 GET /api/papers/{paperId}/pdf
  window.open(getPaperPdfUrl(paperId.value), "_blank", "noopener,noreferrer");
};

const downloadPdf = () => {
  // 打开 GET /api/papers/{paperId}/pdf/download
  window.open(pdfDownloadUrl.value, "_blank", "noopener,noreferrer");
};

const openLayoutTranslation = () => {
  // 打开 GET /api/papers/{paperId}/translation/layout
  window.open(getTranslationLayoutUrl(paperId.value), "_blank", "noopener,noreferrer");
};

const loadPaper = async () => {
  // GET /api/papers/{paperId} -> 论文元数据。
  try {
    paper.value = await store.fetchPaper(paperId.value);
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || "加载论文失败");
    paper.value = null;
  }
};

const loadContent = async (tabName = activeTab.value) => {
  // GET /api/papers/{paperId}/content/{tabName} -> Markdown 内容。
  if (!paperId.value) return;
  contentLoading.value = true;
  try {
    const { data } = await getPaperContent(paperId.value, tabName);
    contents[tabName] = data.content;
  } catch (error) {
    const detail = error?.response?.data?.detail || "加载内容失败";
    ElMessage.error(detail);
  } finally {
    contentLoading.value = false;
  }
};

const sendQuestion = async () => {
  // POST /api/papers/{paperId}/chat，请求体 { question, top_k }。
  if (!question.value.trim()) return;
  const text = question.value.trim();
  messages.value.push({ role: "user", text });
  question.value = "";
  sending.value = true;
  try {
    const { data } = await askPaper(paperId.value, { question: text, top_k: 3 });
    const answerText = data.answer?.startsWith("[")
      ? data.answer
      : `[${generationModel.value}] ${data.answer}`;
    messages.value.push({ role: "assistant", text: answerText });
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || "提问失败");
  } finally {
    sending.value = false;
  }
};

watch(
  () => route.params.paperId,
  async () => {
    // 切换论文时重置对话并重新加载默认摘要页。
    messages.value = [];
    activeTab.value = "summary";
    await loadPaper();
    await loadContent("summary");
  }
);

onMounted(async () => {
  // 工作台开启时锁定 body 滚动，避免三栏布局出现双滚动条。
  document.body.classList.add("workspace-lock");
  await loadPaper();
  await loadContent("summary");
  if (!systemStore.loaded) {
    await systemStore.fetchInfo();
  }
});

onBeforeUnmount(() => {
  // 离开页面时解除 body 锁定。
  document.body.classList.remove("workspace-lock");
});
</script>

<style scoped>
.workspace-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  box-sizing: border-box;
  
  /* 原来是 height: calc(100dvh - var(--top-nav-height)); */
  /* 修改为精确减去外部干扰因素的高度（视你的导航栏实际高度可微调 130px~150px） */
  height: calc(100dvh - 140px); 
  
  min-height: 0;
  overflow: hidden;
}
.workspace-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.page-title {
  margin: 0;
  font-size: 20px;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.model-tag {
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.multi-agent-tag {
  font-weight: 600;
}

.workspace-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr 0.9fr;
  gap: 14px;
  flex: 1;
  min-height: 0;
  align-items: stretch;
  overflow: hidden;
}

.panel {
  background: #ffffff;
  border: 1px solid #d7e3f4;
  border-radius: 14px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.panel-head {
  padding: 12px 14px;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.panel-head h2 {
  margin: 0;
  font-size: 16px;
  color: #0f172a;
}

.panel-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.content-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pdf-frame {
  width: 100%;
  flex: 1;
  border: 0;
  min-height: 0;
  display: block; /* 新增这一行，消除底部的幽灵空白 */
}


.guide-strip {
  margin: 12px;
  margin-bottom: 6px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #edf6ff;
  border: 1px solid #cfe4ff;
  color: #1d4d8f;
  font-size: 13px;
}

.content-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.markdown-body {
  padding: 14px;
  height: 100%;
  overflow: auto;
  scrollbar-gutter: stable;
  line-height: 1.75;
}

.chat-panel {
  min-height: 0;
  overflow: hidden;
}

.chat-subtitle {
  margin: 10px 12px 8px;
  color: #475569;
  font-size: 13px;
}

.chat-box {
  margin: 0 12px;
  flex: 1;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message {
  padding: 8px;
  border-radius: 8px;
}

.message.user {
  background: #e8f1ff;
}

.message.assistant {
  background: #f1f5f9;
}

.message-role {
  font-size: 12px;
  color: #334155;
}

.message-text {
  margin: 6px 0 0;
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 13px;
}
/* 👇 新增以下针对聊天框 Markdown 的样式 👇 */
.chat-markdown {
  white-space: normal; /* 恢复正常的自动换行 */
  line-height: 1.6;
}

/* 使用 :deep() 穿透 scoped 限制，美化注入的 HTML */
.chat-markdown :deep(p) {
  margin: 0 0 8px; /* 段落之间留出呼吸感 */
}
.chat-markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.chat-markdown :deep(ul),
.chat-markdown :deep(ol) {
  margin: 4px 0 8px;
  padding-left: 20px; /* 列表缩进 */
}
.chat-markdown :deep(li) {
  margin-bottom: 4px;
}
.chat-markdown :deep(h1),
.chat-markdown :deep(h2),
.chat-markdown :deep(h3),
.chat-markdown :deep(h4) {
  margin: 12px 0 6px;
  font-size: 14px;
  color: #0f172a;
}
.chat-markdown :deep(strong) {
  font-weight: 600;
  color: #1e293b;
}
/* 👆 新增结束 👆 */
.chat-input {
  margin: 8px 12px 12px;
  padding: 10px 0 0;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  display: grid;
  grid-template-columns: 1fr 96px;
  gap: 8px;
  flex-shrink: 0;
}

.content-panel :deep(.el-tabs) {
  padding: 0 12px;
  flex-shrink: 0;
}

.content-skeleton {
  flex: 1;
  min-height: 0;
}

.content-panel :deep(.el-skeleton__content) {
  height: 100%;
}

.content-panel :deep(.el-tabs__content),
.content-panel :deep(.el-tab-pane) {
  flex: 1;
  min-height: 0;
}

@media (max-width: 1400px) {
  .workspace-grid {
    grid-template-columns: 1fr 1fr;
  }

  .chat-panel {
    grid-column: 1 / -1;
    min-height: 360px;
  }
}

@media (max-width: 980px) {
  .workspace-page {
    height: auto;
    overflow: auto;
  }

  .workspace-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .workspace-grid {
    grid-template-columns: 1fr;
    overflow: visible;
  }

  .panel {
    min-height: 520px;
    height: auto;
  }
}
</style>
