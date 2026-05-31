<template>
  <main class="container dashboard-page">
    <section class="main-column">
      <div class="page-head">
        <h1 class="page-title">论文总览</h1>
        <p class="head-subtitle">集中查看处理过的论文，支持按标题关键词和领域筛选。</p>
      </div>

      <el-card class="filter-card" shadow="never">
        <div class="filters">
          <el-input v-model="keyword" placeholder="按标题搜索..." clearable @clear="onFilterChange" @keyup.enter="onFilterChange" />
          <el-select v-model="tagFilter" clearable placeholder="按领域筛选" @change="onFilterChange">
            <el-option v-for="tag in allTags" :key="tag" :label="tag" :value="tag" />
          </el-select>
          <el-button :loading="store.loading" @click="handleRefresh">刷新</el-button>
        </div>
      </el-card>

      <div class="paper-list-wrapper" v-loading="store.loading">
        <el-empty v-if="filteredPapers.length === 0 && !store.loading" description="暂无论文数据" />

        <div class="grid">
          <el-card v-for="paper in filteredPapers" :key="paper.paper_id" class="paper-card" shadow="hover">
            <h3 class="title" :title="paper.title">{{ paper.title }}</h3>
            <p class="meta"><strong>文件名：</strong>{{ paper.source_filename }}</p>
            <p class="meta"><strong>模板：</strong>{{ paper.summary_template }}</p>
            <p class="meta"><strong>目标语言：</strong>{{ paper.target_language }}</p>
            <p class="meta"><strong>创建时间：</strong>{{ formatTime(paper.created_at) }}</p>
            <div class="tags">
              <el-tag v-for="tag in paper.domain_tags" :key="`${paper.paper_id}-${tag}`" size="small" effect="plain">
                {{ tag }}
              </el-tag>
            </div>
            <div class="foot">
              <template v-if="paper.status === 'processing' && progressMap[paper.paper_id]">
                <div class="progress-wrap">
                  <el-progress
                    :percentage="progressMap[paper.paper_id].progress"
                    :stroke-width="10"
                    :status="progressMap[paper.paper_id].status === 'failed' ? 'exception' : undefined"
                    :color="progressMap[paper.paper_id].status === 'failed' ? '#f56c6c' : '#409eff'"
                  />
                  <span class="progress-label">{{ progressMap[paper.paper_id].label }}</span>
                </div>
              </template>
              <template v-else>
                <el-tag :type="statusTypeMap[paper.status] || 'info'">{{ statusLabel(paper.status) }}</el-tag>
              </template>
              <div class="foot-actions">
                <el-button type="primary" size="small" @click="goWorkspace(paper.paper_id)">进入工作台</el-button>
                <el-tooltip content="删除论文" placement="top">
                  <el-button size="small" type="danger" plain @click="handleDelete(paper)">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </el-tooltip>
              </div>
            </div>
          </el-card>
        </div>
      </div>

      <div class="pagination-row" v-if="store.totalPages > 1">
        <el-pagination
          v-model:current-page="store.page"
          v-model:page-size="store.pageSize"
          :page-sizes="[8, 12, 24, 48]"
          :total="store.total"
          layout="total, sizes, prev, pager, next"
          background
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </section>

    <SystemGuidePanel class="side-column" :info="systemStore.info" />
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { Delete } from "@element-plus/icons-vue";

import { API_BASE_URL, deletePaper } from "../api/client";
import { usePaperStore } from "../stores/papers";
import { useSystemStore } from "../stores/system";
import SystemGuidePanel from "../components/SystemGuidePanel.vue";

const router = useRouter();
const store = usePaperStore();
const systemStore = useSystemStore();

const keyword = ref("");
const tagFilter = ref("");

const statusTypeMap = { completed: "success", processing: "warning", failed: "danger" };
const statusTextMap = { completed: "已完成", processing: "处理中", failed: "处理失败" };
const statusLabel = (status) => statusTextMap[status] || status;

const statusLabelMap = {
  queued: "排队中",
  parsing: "解析中",
  translating: "翻译中",
  summarizing: "总结中",
  critiquing: "建议生成中",
  done: "已完成",
  failed: "失败",
};

// SSE 进度追踪
const progressMap = reactive({});
const streamMap = {};

const subscribeToTask = (paperId, taskId) => {
  if (streamMap[paperId]) return;
  const token = localStorage.getItem("access_token");
  const es = new EventSource(`${API_BASE_URL}/api/tasks/${taskId}/events?token=${token}`);
  streamMap[paperId] = es;
  es.addEventListener("progress", async (event) => {
    const payload = JSON.parse(event.data);
    const label = statusLabelMap[payload.status] || payload.status;
    progressMap[paperId] = {
      progress: payload.progress,
      status: payload.status,
      label: `${label} ${payload.progress}%`,
    };
    if (payload.status === "done" || payload.status === "failed") {
      es.close();
      delete streamMap[paperId];
      delete progressMap[paperId];
      await store.fetchPapers(store.page, store.pageSize);
      subscribeAll();
    }
  });
  es.onerror = () => {
    es.close();
    delete streamMap[paperId];
  };
};

const subscribeAll = () => {
  for (const paper of store.papers) {
    if (paper.status === "processing" && paper.task_id) {
      subscribeToTask(paper.paper_id, paper.task_id);
    }
  }
};

const unsubscribeAll = () => {
  for (const [paperId, es] of Object.entries(streamMap)) {
    es.close();
    delete streamMap[paperId];
  }
  Object.keys(progressMap).forEach((k) => delete progressMap[k]);
};

const allTags = computed(() => {
  const values = new Set();
  for (const paper of store.papers) {
    for (const tag of (paper.domain_tags || [])) {
      if (tag && tag !== "General") values.add(tag);
    }
  }
  // General 放最后
  const hasGeneral = store.papers.some(p => (p.domain_tags || []).includes("General"));
  const sorted = [...values].sort();
  if (hasGeneral) sorted.push("General");
  return sorted;
});

const filteredPapers = computed(() =>
  store.papers.filter((paper) => {
    const matchesKeyword = paper.title.toLowerCase().includes(keyword.value.trim().toLowerCase());
    const tags = paper.domain_tags || [];
    const matchesTag = !tagFilter.value || tags.includes(tagFilter.value);
    return matchesKeyword && matchesTag;
  })
);

const goWorkspace = (paperId) => router.push(`/workspace/${paperId}`);
const formatTime = (iso) => new Date(iso).toLocaleString();

const onPageChange = (page) => store.fetchPapers(page);
const onSizeChange = (size) => store.fetchPapers(1, size);
const onFilterChange = () => store.fetchPapers(1);
const handleRefresh = async () => {
  unsubscribeAll();
  await store.fetchPapers();
  subscribeAll();
};

const handleDelete = async (paper) => {
  try {
    await ElMessageBox.confirm(
      `您确定要删除论文《${paper.title}》及相关的解析数据吗？此操作不可恢复。`,
      "确认删除",
      { confirmButtonText: "确定删除", cancelButtonText: "取消", type: "warning" }
    );
    await deletePaper(paper.paper_id);
    ElMessage.success("论文已删除");
    await store.fetchPapers();
  } catch (e) {
    if (e !== "cancel") {
      ElMessage.error(e.response?.data?.detail || "删除失败");
    }
  }
};

onMounted(async () => {
  await store.fetchPapers();
  subscribeAll();
  if (!systemStore.loaded) {
    await systemStore.fetchInfo();
  }
});

onBeforeUnmount(() => {
  unsubscribeAll();
});
</script>

<style scoped>
.dashboard-page {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
  gap: 18px;
  height: calc(100vh - 60px);
}

.main-column {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
}

.head-subtitle { margin-top: 8px; color: #475569; }

.filter-card { border-radius: 14px; border: 1px solid #d3def1; flex-shrink: 0; }
.filters { display: grid; grid-template-columns: 1fr 250px 110px; gap: 10px; }

/* 论文列表滚动容器 */
.paper-list-wrapper {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
}

.paper-list-wrapper::-webkit-scrollbar { width: 6px; }
.paper-list-wrapper::-webkit-scrollbar-thumb { background-color: #cbd5e1; border-radius: 4px; }
.paper-list-wrapper::-webkit-scrollbar-track { background: transparent; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px;
}

.paper-card {
  border-radius: 12px;
  border: 1px solid #dae5f6;
}

.title {
  margin: 0 0 10px;
  font-size: 16px;
  color: #0f172a;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.meta { margin: 4px 0; color: #334155; font-size: 13px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }

.foot {
  margin-top: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.foot-actions { display: flex; gap: 6px; }

.progress-wrap {
  flex: 1;
  min-width: 0;
  margin-right: 8px;
}
.progress-wrap .el-progress { margin-bottom: 2px; }
.progress-label {
  font-size: 12px;
  color: #475569;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pagination-row {
  display: flex;
  justify-content: center;
  padding: 8px 0;
  flex-shrink: 0;
}

@media (max-width: 1100px) { .dashboard-page { grid-template-columns: 1fr; } }
@media (max-width: 768px) { .filters { grid-template-columns: 1fr; } }
</style>
