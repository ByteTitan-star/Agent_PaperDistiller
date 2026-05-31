<template>
  <main class="workspace-page">
    <header class="workspace-head">
      <div class="head-left">
        <el-button @click="$router.push('/dashboard')">返回总览</el-button>
        <h1 class="page-title">{{ paper?.title || '阅读工作台' }}</h1>
      </div>
      <div class="head-right">
        <el-tag :type="statusTypeMap[paper?.status] || 'info'" size="small">{{ statusLabel(paper?.status) }}</el-tag>
        <el-tooltip :content="modelListLabel" placement="bottom">
          <el-tag class="model-tag" effect="plain" size="small">{{ generationModel }}</el-tag>
        </el-tooltip>
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
          <div class="chat-head-actions">
            <el-switch
              v-model="deepSearch"
              active-text="深度搜索"
              inactive-text=""
              style="--el-switch-on-color: #6366f1"
            />
          </div>
        </div>

        <div class="chat-box" ref="chatBoxRef">
          <!-- 欢迎消息 -->
          <div v-if="messages.length === 0" class="welcome-hint">
            <p>👋 你好！我是Agent PaperDistriller助手，可以回答关于这篇论文的任何问题。</p>
            <p>也可以聊任何你想了解的话题。</p>
          </div>

          <div v-for="(item, idx) in messages" :key="idx" :class="['message', item.role]">
            <!-- 头像 -->
            <div class="avatar">
              <span v-if="item.role === 'user'">你</span>
              <span v-else>AI</span>
            </div>

            <div class="bubble">
              <!-- ====== 深度研究模式 ====== -->
              <template v-if="item._isResearch">
                <!-- 研究阶段时间线 -->
                <div class="research-timeline" v-if="item._phases?.length">
                  <div
                    v-for="(p, pi) in item._phases"
                    :key="pi"
                    :class="['timeline-step', { active: pi === item._phases.length - 1 && item._streaming }]"
                  >
                    <span class="timeline-icon">{{ phaseIcon(p.phase) }}</span>
                    <span class="timeline-label">{{ p.label }}</span>
                    <span v-if="p.detail" class="timeline-detail">{{ p.detail }}</span>
                  </div>
                </div>

                <!-- 来源卡片 -->
                <div v-if="item._sources?.length" class="sources-section">
                  <el-collapse class="sources-collapse">
                    <el-collapse-item :title="`📚 参考来源（${item._sources.length}）`">
                      <div v-for="(s, si) in item._sources" :key="si" class="source-card">
                        <span class="source-icon">🔍</span>
                        <a v-if="s.url" :href="s.url" target="_blank" rel="noopener" class="source-link">{{ s.title || s.snippet || '搜索结果' }}</a>
                        <span v-else class="source-text">{{ s.snippet || s.title || '搜索结果' }}</span>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </div>

                <!-- 工具使用提示 -->
                <div v-if="item._toolHint && item._streaming" class="tool-hint">{{ item._toolHint }}</div>

                <!-- 研究报告正文 -->
                <div v-if="item.text" class="research-report">
                  <div class="chat-markdown" v-html="renderChatMarkdown(item.text)"></div>
                </div>

                <!-- 流式光标 -->
                <span v-if="item._streaming && item.text" class="streaming-cursor">▍</span>
              </template>

              <!-- ====== 普通对话模式 ====== -->
              <template v-else>
                <div v-if="item._toolHint" class="tool-hint">{{ item._toolHint }}</div>
                <div
                  v-if="item.role === 'assistant'"
                  class="bubble-text chat-markdown"
                  v-html="renderChatMarkdown(item.text)"
                ></div>
                <pre v-else class="bubble-text">{{ item.text }}</pre>
                <span v-if="item._streaming" class="streaming-cursor">▍</span>
              </template>

              <!-- Thinking Chain（两种模式通用） -->
              <el-collapse v-if="item.thinking?.length" class="thinking-collapse">
                <el-collapse-item title="💭 查看推理过程">
                  <div v-for="(step, si) in item.thinking" :key="si" class="thinking-step">
                    {{ step }}
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>
          </div>

        </div>

        <!-- 快捷追问胶囊 -->
        <div v-if="messages.length > 0 && !sending" class="suggestions">
          <button
            v-for="s in suggestions"
            :key="s"
            class="pill"
            @click="question = s; sendQuestion()"
          >{{ s }}</button>
        </div>

        <div class="chat-input">
          <div class="textarea-wrap">
            <textarea
              ref="textareaRef"
              v-model="question"
              class="auto-textarea"
              placeholder="输入您想问的问题..."
              @keydown="handleKeydown"
              @input="autoResize"
              rows="1"
            ></textarea>
          </div>
          <el-button type="primary" :loading="sending" @click="sendQuestion" :disabled="!question.trim()">发送</el-button>
        </div>
      </aside>
    </section>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import MarkdownIt from "markdown-it";

import {
  askPaper,
  askPaperStream,
  getPaperContent,
  getPaperPdfDownloadUrl,
  getPaperPdfUrl,
  getTranslationLayoutUrl,
  listChatSessions,
  getChatMessages,
} from "../api/client";
import { usePaperStore } from "../stores/papers";
import { useSystemStore } from "../stores/system";

// Markdown 渲染器：禁用原始 HTML，避免直接渲染后端返回中的 HTML 片段。
const md = new MarkdownIt({ html: false, breaks: false, linkify: true });

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
const deepSearch = ref(false);
const textareaRef = ref(null);
const chatBoxRef = ref(null);
const messages = ref([]);
const sessionId = ref(null);

// 加载最近一次会话的历史消息
const loadChatHistory = async () => {
  if (!paperId.value) return;
  try {
    const { data: sessions } = await listChatSessions(paperId.value);
    if (sessions && sessions.length > 0) {
      const latest = sessions[0];
      sessionId.value = latest.session_id;
      const { data: historyMsgs } = await getChatMessages(latest.session_id);
      if (historyMsgs && historyMsgs.length > 0) {
        messages.value = historyMsgs.map((m) => ({
          role: m.role,
          text: m.content,
          thinking: m.thinking_chain,
          _streaming: false,
        }));
      }
    }
  } catch {
    // 静默失败，不影响正常使用
  }
};

// 快捷追问建议
const suggestions = computed(() => {
  if (messages.value.length === 0) return [];
  const base = ["这篇论文的核心创新点是什么？", "方法有哪些局限性？", "实验结果如何？"];
  if (deepSearch.value) return [...base, "最新相关研究有哪些？"];
  return base;
});

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

const renderChatMarkdown = (source) => {
  if (!source) return "";
  // 清理 LLM 输出的连续多余空行
  return md.render(source.replace(/\n{3,}/g, "\n\n"));
};

const scrollToBottom = () => {
  nextTick(() => {
    if (chatBoxRef.value) chatBoxRef.value.scrollTop = chatBoxRef.value.scrollHeight;
  });
};

const phaseIcon = (phase) => {
  const map = { planning: "🔍", clarifying: "💡", searching: "🌐", analyzing: "🧠", generating: "✍️" };
  return map[phase] || "⏳";
};

const handleKeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
};

const autoResize = () => {
  const el = textareaRef.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 120) + "px";
};

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
  if (!question.value.trim() || sending.value) return;
  const text = question.value.trim();
  messages.value.push({ role: "user", text });
  question.value = "";
  sending.value = true;
  if (textareaRef.value) textareaRef.value.style.height = "auto";
  scrollToBottom();

  const isDeep = deepSearch.value;
  const assistantIdx = messages.value.length;

  if (isDeep) {
    // 深度研究：创建带时间线的研究消息
    messages.value.push({
      role: "assistant", text: "", _streaming: true, _isResearch: true,
      _phases: [], _sources: [], _currentPhase: "",
    });
  } else {
    messages.value.push({ role: "assistant", text: "", _streaming: true });
  }
  scrollToBottom();

  try {
    await askPaperStream(
      paperId.value,
      { question: text, top_k: 8, deep_search: isDeep, session_id: sessionId.value },
      (event) => {
        const msg = messages.value[assistantIdx];
        if (event.type === "phase") {
          msg._phases.push({ phase: event.phase, label: event.label, detail: event.detail || "" });
          msg._currentPhase = event.phase;
          scrollToBottom();
        } else if (event.type === "source") {
          msg._sources.push(event);
          scrollToBottom();
        } else if (event.type === "tool") {
          msg._toolHint = `🔍 正在搜索：${event.query || event.name}`;
          scrollToBottom();
        } else if (event.type === "token") {
          msg.text += event.text;
          scrollToBottom();
        } else if (event.type === "done") {
          msg.text = event.answer || msg.text;
          msg._streaming = false;
          if (event.thinking_chain) msg.thinking = event.thinking_chain;
          if (event.session_id) sessionId.value = event.session_id;
          scrollToBottom();
        } else if (event.type === "error") {
          msg.text = event.text;
          msg._streaming = false;
          ElMessage.error(event.text);
        }
      }
    );
  } catch (error) {
    messages.value[assistantIdx].text = error?.response?.data?.detail || "提问失败";
    messages.value[assistantIdx]._streaming = false;
    ElMessage.error(error?.response?.data?.detail || "提问失败");
  } finally {
    messages.value[assistantIdx]._streaming = false;
    sending.value = false;
    scrollToBottom();
  }
};

watch(
  () => route.params.paperId,
  async () => {
    // 切换论文时重置对话并重新加载默认摘要页。
    messages.value = [];
    sessionId.value = null;
    activeTab.value = "summary";
    await loadPaper();
    await loadContent("summary");
    await loadChatHistory();
  }
);

onMounted(async () => {
  // 工作台开启时锁定 body 滚动，避免三栏布局出现双滚动条。
  document.body.classList.add("workspace-lock");
  await loadPaper();
  await loadContent("summary");
  await loadChatHistory();
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
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
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

.chat-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.chat-box {
  margin: 0 12px;
  flex: 1;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 8px 0;
}

/* 欢迎提示 */
.welcome-hint {
  text-align: center;
  color: #94a3b8;
  font-size: 13px;
  padding: 40px 20px;
  line-height: 1.8;
}
.welcome-hint p { margin: 0; }

/* 消息行 */
.message {
  display: flex;
  gap: 8px;
  max-width: 95%;
}
.message.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}
.message.assistant {
  align-self: flex-start;
}

/* 头像 */
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
  color: #fff;
}
.message.user .avatar { background: #6366f1; }
.message.assistant .avatar { background: #0ea5e9; }

/* 气泡 */
.bubble {
  padding: 10px 14px;
  border-radius: 14px;
  max-width: 100%;
  min-width: 60px;
}
.message.user .bubble {
  background: #6366f1;
  color: #fff;
  border-bottom-right-radius: 4px;
}
.message.assistant .bubble {
  background: #f1f5f9;
  color: #1e293b;
  border-bottom-left-radius: 4px;
}

.bubble-text {
  margin: 0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
}

/* Markdown 渲染内容：关闭 pre-wrap，让 MarkdownIt 控制换行 */
.chat-markdown {
  white-space: normal;
  line-height: 1.55;
  font-size: 13.5px;
}

/* 用户消息保持 pre-wrap（纯文本） */
.message.user .bubble-text {
  white-space: pre-wrap;
}

/* 打字动画 */
.typing-dots {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}
.typing-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
  animation: dot-blink 1.4s infinite both;
}
.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes dot-blink {
  0%, 80%, 100% { opacity: 0.3; }
  40% { opacity: 1; }
}

/* 深度搜索进度 */
.search-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #6366f1;
  margin-top: 6px;
}
.search-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #c7d2fe;
  border-top-color: #6366f1;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* 流式光标 */
.streaming-cursor {
  display: inline;
  animation: cursor-blink 0.8s step-end infinite;
  color: #6366f1;
  font-weight: bold;
}
@keyframes cursor-blink {
  50% { opacity: 0; }
}

/* 工具使用提示 */
.tool-hint {
  font-size: 12px;
  color: #6366f1;
  background: #f5f3ff;
  padding: 4px 8px;
  border-radius: 6px;
  margin-bottom: 6px;
  display: inline-block;
}

/* ====== 深度研究 UI ====== */
.research-timeline {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
  padding: 8px 0;
}
.timeline-step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #64748b;
  padding: 4px 0;
  transition: color 0.3s;
}
.timeline-step.active {
  color: #6366f1;
  font-weight: 500;
}
.timeline-icon { font-size: 16px; flex-shrink: 0; }
.timeline-label { flex: 1; }
.timeline-detail {
  font-size: 12px;
  color: #94a3b8;
  max-width: 200px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sources-section { margin-bottom: 8px; }
.sources-collapse {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}
.sources-collapse :deep(.el-collapse-item__header) {
  font-size: 13px;
  color: #475569;
  height: 34px;
  line-height: 34px;
  padding: 0 10px;
  background: #f8fafc;
  border-bottom: none;
}
.sources-collapse :deep(.el-collapse-item__wrap) { border-bottom: none; }
.sources-collapse :deep(.el-collapse-item__content) { padding: 6px 10px; }
.source-card {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 0;
  font-size: 12px;
  color: #475569;
  line-height: 1.4;
  border-bottom: 1px dashed #f1f5f9;
}
.source-card:last-child { border-bottom: none; }
.source-icon { flex-shrink: 0; }
.source-text { flex: 1; word-break: break-word; }
.source-link { flex: 1; word-break: break-word; color: #3b82f6; text-decoration: none; }
.source-link:hover { text-decoration: underline; }

.research-report {
  padding-top: 4px;
  border-top: 2px solid #e2e8f0;
  margin-top: 6px;
}

/* 思考过程 */
.thinking-collapse {
  margin-top: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}
.thinking-collapse :deep(.el-collapse-item__header) {
  font-size: 12px;
  color: #6366f1;
  height: 32px;
  line-height: 32px;
  padding: 0 10px;
  background: #f5f3ff;
  border-bottom: none;
}
.thinking-collapse :deep(.el-collapse-item__wrap) { border-bottom: none; }
.thinking-collapse :deep(.el-collapse-item__content) { padding: 8px 10px; }
.thinking-step {
  font-size: 12px;
  color: #475569;
  padding: 4px 0;
  line-height: 1.5;
  border-bottom: 1px dashed #e2e8f0;
}
.thinking-step:last-child { border-bottom: none; }

/* 快捷追问胶囊 */
.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 6px 12px;
}
.pill {
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 4px 12px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.pill:hover {
  background: #e0e7ff;
  border-color: #a5b4fc;
  color: #4338ca;
}

/* 输入区域 */
.chat-input {
  margin: 8px 12px 12px;
  padding: 10px 0 0;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  display: grid;
  grid-template-columns: 1fr 72px;
  gap: 8px;
  flex-shrink: 0;
  align-items: end;
}

.textarea-wrap {
  position: relative;
}
.auto-textarea {
  width: 100%;
  min-height: 36px;
  max-height: 120px;
  padding: 8px 10px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  resize: none;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.5;
  outline: none;
  box-sizing: border-box;
  transition: border-color 0.2s;
}
.auto-textarea:focus {
  border-color: #6366f1;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.1);
}
/* 用户气泡内的 Markdown（白色文字） */
.message.user .bubble :deep(a) { color: #c7d2fe; }
.message.user .bubble :deep(strong) { color: #fff; }

/* 助手气泡内的 Markdown — 紧凑学术排版 */
.chat-markdown :deep(p) { margin: 0 0 6px; }
.chat-markdown :deep(p:last-child) { margin-bottom: 0; }
.chat-markdown :deep(ul),
.chat-markdown :deep(ol) { margin: 2px 0 6px; padding-left: 18px; }
.chat-markdown :deep(li) { margin-bottom: 2px; }
.chat-markdown :deep(li > p) { margin: 0; } /* 防止 li 内部 p 标签撑大间距 */
.chat-markdown :deep(h1),
.chat-markdown :deep(h2),
.chat-markdown :deep(h3),
.chat-markdown :deep(h4) { margin: 8px 0 4px; font-size: 14px; color: #0f172a; }
.chat-markdown :deep(h2:first-child),
.chat-markdown :deep(h3:first-child) { margin-top: 0; }
.chat-markdown :deep(strong) { font-weight: 600; color: #1e293b; }
.chat-markdown :deep(hr) { border: none; border-top: 1px solid #e2e8f0; margin: 8px 0; }
.chat-markdown :deep(blockquote) {
  margin: 4px 0 6px;
  padding: 2px 10px;
  border-left: 3px solid #c7d2fe;
  background: #f8fafc;
  color: #475569;
}
.chat-markdown :deep(code) {
  background: #f1f5f9;
  padding: 1px 4px;
  border-radius: 4px;
  font-size: 12px;
}
.chat-markdown :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  padding: 10px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 12px;
  margin: 4px 0 6px;
}
.chat-markdown :deep(pre code) {
  background: transparent;
  padding: 0;
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
