<template>
  <main class="container home-page">
    <section class="main-column">
      <div class="hero-section">
        <h1 class="hero-title">Elevate Your Research.</h1>
        <p class="hero-subtitle">
          上传您的论文以启动多智能体分析。系统将自动执行：全文解析、精准翻译、核心思路总结与深度的研究方法评估。
        </p>
      </div>

      <div class="upload-workspace glass-panel">
        <div class="config-bar">
          <el-form :inline="true" class="elegant-form">
            <el-form-item label="Target Language">
              <el-select v-model="targetLanguage" style="width: 160px" effect="light">
                <el-option label="中文 (Chinese)" value="中文" />
                <el-option label="英语 (English)" value="英文" />
                <el-option label="日语 (Japanese)" value="日文" />
              </el-select>
            </el-form-item>
            <el-form-item label="Output Template">
              <el-select v-model="summaryTemplate" style="width: 200px" effect="light">
                <el-option v-for="template in templates" :key="template.name" :label="template.name" :value="template.name" />
              </el-select>
            </el-form-item>
          </el-form>
        </div>

        <el-upload class="elegant-upload" drag :limit="1" accept=".pdf" :http-request="onUploadRequest">
          <div class="upload-content">
            <div class="upload-icon">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>
            </div>
            <h3 class="upload-title">Drop your PDF here</h3>
            <p class="upload-desc">Max file size: 50MB. Dual-column formats are fully supported.</p>
          </div>
        </el-upload>

        <div class="status-panel" v-if="taskId">
          <div class="status-header">
            <span class="status-label">当前任务状态</span>
            <span class="status-text">{{ statusText }}</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" :class="progressStatus" :style="{ width: progress + '%' }"></div>
          </div>
        </div>

        <div class="action-footer">
          <button class="btn-glass primary" :disabled="!paperId || progress < 100" @click="goWorkspace">
            启动阅读工作台 ✨
          </button>
          <button class="btn-glass secondary" @click="$router.push('/dashboard')">
            查看论文总览
          </button>
        </div>
      </div>
    </section>

    <SystemGuidePanel class="side-column glass-panel" style="padding: 28px;" :info="systemStore.info" />
  </main>
</template>



<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { UploadFilled } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";

import { API_BASE_URL, listTemplates, uploadPaper } from "../api/client";
import { usePaperStore } from "../stores/papers";
import { useSystemStore } from "../stores/system";
import SystemGuidePanel from "../components/SystemGuidePanel.vue";

// 路由 + 全局状态：用于页面跳转、论文缓存和系统信息展示。
const router = useRouter();
const store = usePaperStore();
const systemStore = useSystemStore();

// 上传配置：目标语言 + 摘要模板（最终会作为 multipart 表单字段提交给后端）。
const targetLanguage = ref("中文");
const summaryTemplate = ref("tinghua.md");
const templates = ref([{ name: "tinghua.md" }]);

// 任务状态：来自 `/api/upload` 返回的 task_id/paper_id 与 SSE 进度流。
const taskId = ref("");
const paperId = ref("");
const progress = ref(0);
const statusText = ref("当前无运行任务");
const progressStatus = ref("");

let eventSource = null;

// 后端状态码到页面文案的映射。
const statusLabelMap = {
  queued: "排队中",
  parsing: "解析中",
  translating: "翻译中",
  summarizing: "总结中",
  critiquing: "建议生成中",
  done: "已完成",
  failed: "失败"
};

const loadTemplates = async () => {
  // GET /api/templates -> [{name}]，用于模板下拉框。
  const { data } = await listTemplates();
  templates.value = data;
  if (!templates.value.some((item) => item.name === summaryTemplate.value) && templates.value.length > 0) {
    summaryTemplate.value = templates.value[0].name;
  }
};

const closeTaskStream = () => {
  // 离开页面或任务结束时关闭 SSE，避免重复连接和内存泄漏。
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
};

const openTaskStream = (newTaskId) => {
  // 订阅 GET /api/tasks/{taskId}/events（SSE），实时接收 progress/status/message。
  closeTaskStream();
  eventSource = new EventSource(`${API_BASE_URL}/api/tasks/${newTaskId}/events`);
  eventSource.addEventListener("progress", async (event) => {
    const payload = JSON.parse(event.data);
    progress.value = payload.progress;
    const statusTextRaw = statusLabelMap[payload.status] || payload.status;
    statusText.value = `${statusTextRaw}：${payload.message}`;
    if (payload.status === "failed") {
      progressStatus.value = "exception";
      ElMessage.error(payload.message);
      closeTaskStream();
      return;
    }
    if (payload.status === "done") {
      progressStatus.value = "success";
      await store.fetchPapers();
      closeTaskStream();
    }
  });
  eventSource.onerror = () => {
    closeTaskStream();
  };
};

const onUploadRequest = async ({ file, onSuccess, onError }) => {
  // 自定义上传流程：POST /api/upload -> { task_id, paper_id }。
  try {
    progress.value = 0;
    progressStatus.value = "";
    const { data } = await uploadPaper(file, targetLanguage.value, summaryTemplate.value);
    taskId.value = data.task_id;
    paperId.value = data.paper_id;
    statusText.value = "任务已创建，正在排队";
    openTaskStream(taskId.value);
    onSuccess();
  } catch (error) {
    const message = error?.response?.data?.detail || error.message || "上传失败";
    ElMessage.error(message);
    onError(error);
  }
};

const goWorkspace = () => {
  // 进入论文工作台页面，URL 参数为 paper_id。
  router.push(`/workspace/${paperId.value}`);
};

onMounted(async () => {
  // 首次进入页面时加载模板、刷新论文列表、拉取系统信息。
  await loadTemplates();
  await store.fetchPapers();
  if (!systemStore.loaded) {
    await systemStore.fetchInfo();
  }
});

onBeforeUnmount(() => {
  // 组件卸载时兜底关闭 SSE。
  closeTaskStream();
});
</script>



<style scoped>
.home-page {
  display: grid;
  /* 左侧自适应，右侧固定为 420px（原来是 340px） */
  grid-template-columns: minmax(0, 1fr) 420px; 
  /* 增加中间的留白间距，缓解拥挤感（原来是 32px） */
  gap: 48px; 
  align-items: start;
}

.hero-section {
  text-align: center;
  margin-bottom: 40px;
  animation: slideDown 0.8s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slideDown {
  from { opacity: 0; transform: translateY(-20px); }
  to { opacity: 1; transform: translateY(0); }
}

.hero-title {
  font-family: var(--font-heading);
  font-size: 48px; /* 中文字体相对英文可以稍微小一点点，更显精致 */
  font-weight: 600;
  letter-spacing: -1px;
  color: var(--text-main);
  margin: 0 0 16px 0;
  background: linear-gradient(135deg, #1e293b, #64748b);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-subtitle {
  font-size: 18px;
  color: var(--text-muted);
  max-width: 600px;
  margin: 0 auto;
  line-height: 1.5;
}

.upload-workspace {
  padding: 40px;
  animation: fadeUp 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.1s backwards;
}

@keyframes fadeUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.config-bar {
  margin-bottom: 24px;
  display: flex;
  justify-content: center;
}

/* 覆盖 Element UI 表单样式以契合通透感 */
:deep(.elegant-form .el-form-item__label) {
  font-family: var(--font-body);
  font-weight: 500;
  color: var(--text-muted);
}
:deep(.elegant-form .el-input__wrapper) {
  background: rgba(255, 255, 255, 0.5);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.8) inset;
  border-radius: 12px;
  backdrop-filter: blur(4px);
}
:deep(.elegant-form .el-input__inner) {
  font-family: var(--font-body);
  color: var(--text-main);
  font-weight: 500;
}

/* 极简磨砂上传框 */
:deep(.elegant-upload .el-upload-dragger) {
  background: rgba(255, 255, 255, 0.3);
  border: 2px dashed rgba(255, 255, 255, 0.8);
  border-radius: 20px;
  padding: 60px 20px;
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
:deep(.elegant-upload .el-upload-dragger:hover),
:deep(.elegant-upload .el-upload-dragger.is-dragover) {
  background: rgba(255, 255, 255, 0.7);
  border-color: #a855f7;
  transform: translateY(-2px);
  box-shadow: 0 10px 30px rgba(168, 85, 247, 0.1);
}

.upload-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.upload-icon {
  width: 64px;
  height: 64px;
  background: #ffffff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #a855f7;
  box-shadow: 0 8px 20px rgba(168, 85, 247, 0.15);
  margin-bottom: 8px;
}

.upload-title {
  margin: 0;
  font-family: var(--font-heading);
  font-size: 22px;
  font-weight: 600;
  color: var(--text-main);
}

.upload-desc {
  margin: 0;
  color: var(--text-muted);
  font-size: 14px;
}

/* 圆润的进度条设计 */
.status-panel {
  margin-top: 32px;
  padding: 20px;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.8);
}

.status-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 12px;
  font-weight: 500;
}

.status-label { color: var(--text-muted); }
.status-text { color: var(--text-main); }

.progress-bar-bg {
  height: 8px;
  background: rgba(0, 0, 0, 0.05);
  border-radius: 99px;
  overflow: hidden;
}

.progress-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #a855f7, #ec4899);
  border-radius: 99px;
  transition: width 0.4s ease;
}
.progress-bar-fill.exception { background: #ef4444; }

/* 玻璃质感按钮 */
.action-footer {
  margin-top: 32px;
  display: flex;
  justify-content: center;
  gap: 16px;
}

.btn-glass {
  font-family: var(--font-body);
  font-weight: 600;
  font-size: 15px;
  padding: 14px 28px;
  border-radius: 100px;
  cursor: pointer;
  transition: all 0.3s ease;
  border: none;
}

.btn-glass.primary {
  background: linear-gradient(135deg, #1e293b, #0f172a);
  color: #ffffff;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.2);
}
.btn-glass.primary:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.3);
}
.btn-glass.primary:disabled {
  background: rgba(15, 23, 42, 0.4);
  cursor: not-allowed;
  box-shadow: none;
}

.btn-glass.secondary {
  background: rgba(255, 255, 255, 0.8);
  color: var(--text-main);
  border: 1px solid rgba(255, 255, 255, 1);
}
.btn-glass.secondary:hover {
  background: #ffffff;
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(0,0,0,0.05);
}
</style>
