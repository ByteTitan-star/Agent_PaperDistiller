<template>
  <main class="settings-page">
    <div class="settings-container">
      <!-- 面包屑导航 -->
      <nav class="breadcrumb">
        <RouterLink to="/dashboard" class="crumb">总览</RouterLink>
        <span class="crumb-sep">/</span>
        <span class="crumb current">用户设置</span>
      </nav>

      <!-- 两栏布局 -->
      <div class="two-col-layout">
        <!-- 左侧：API 密钥配置 -->
        <section class="config-card api-card" v-loading="loading">
          <div class="card-header">
            <h2>API 密钥配置</h2>
            <p class="card-desc">配置个人密钥后系统优先使用您的配置。留空则使用系统默认。</p>
          </div>

          <!-- DeepSeek -->
          <div class="provider-block">
            <div class="provider-head">
              <span class="provider-indicator deepseek" />
              <span class="provider-name">DeepSeek</span>
              <el-tag v-if="keyStatus.deepseek" type="success" size="small" class="key-status">已配置</el-tag>
              <el-tag v-else type="info" size="small" class="key-status">未配置</el-tag>
            </div>
            <div class="provider-fields">
              <el-form-item label="API Key">
                <el-input v-model="form.deepseek_api_key" placeholder="sk-..." show-password />
              </el-form-item>
              <el-form-item label="Base URL">
                <el-input v-model="form.deepseek_base_url" placeholder="https://api.deepseek.com" />
              </el-form-item>
            </div>
          </div>

          <!-- Qwen -->
          <div class="provider-block">
            <div class="provider-head">
              <span class="provider-indicator qwen" />
              <span class="provider-name">Qwen (通义千问)</span>
              <el-tag v-if="keyStatus.qwen" type="success" size="small" class="key-status">已配置</el-tag>
              <el-tag v-else type="info" size="small" class="key-status">未配置</el-tag>
            </div>
            <div class="provider-fields">
              <el-form-item label="API Key">
                <el-input v-model="form.qwen_api_key" placeholder="sk-..." show-password />
              </el-form-item>
              <el-form-item label="Base URL">
                <el-input v-model="form.qwen_base_url" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
              </el-form-item>
            </div>
          </div>

          <!-- Tavily -->
          <div class="provider-block">
            <div class="provider-head">
              <span class="provider-indicator tavily" />
              <span class="provider-name">Tavily (Web Search)</span>
              <el-tag v-if="keyStatus.tavily" type="success" size="small" class="key-status">已配置</el-tag>
              <el-tag v-else type="info" size="small" class="key-status">未配置</el-tag>
            </div>
            <div class="provider-fields">
              <el-form-item label="API Key">
                <el-input v-model="form.tavily_api_key" placeholder="tvly-..." show-password />
              </el-form-item>
            </div>
          </div>

          <div class="save-row">
            <el-button size="large" @click="resetKeys" class="reset-btn">
              重置
            </el-button>
            <el-button type="primary" size="large" @click="saveKeys" :loading="saving" class="save-btn">
              保存配置
            </el-button>
          </div>
        </section>

        <!-- 右侧：模板管理 -->
        <section class="config-card tpl-card">
          <div class="card-header">
            <h2>模板管理</h2>
            <p class="card-desc">管理自定义摘要模板，仅支持 .md 文件。系统模板对所有用户可见。</p>
          </div>

          <div class="tpl-actions">
            <el-button type="primary" @click="showCreateDialog">
              <el-icon style="margin-right: 4px;"><Plus /></el-icon>创建模板
            </el-button>
            <el-upload
              :show-file-list="false"
              :before-upload="beforeUploadTemplate"
              :http-request="handleUploadTemplate"
              accept=".md"
            >
              <el-button>
                <el-icon style="margin-right: 4px;"><Upload /></el-icon>上传 .md
              </el-button>
            </el-upload>
          </div>

          <el-table
            :data="templateList"
            stripe
            size="small"
            v-loading="tplLoading"
            empty-text="暂无模板"
            :max-height="460"
            class="tpl-table"
          >
            <el-table-column prop="name" label="模板名称" min-width="160" show-overflow-tooltip />
            <el-table-column prop="domain_tag" label="领域" min-width="120" show-overflow-tooltip />
            <el-table-column label="类型" width="80" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.is_system" type="info" size="small" effect="plain">系统</el-tag>
                <el-tag v-else type="success" size="small" effect="plain">个人</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="180" align="center" fixed="right">
              <template #default="{ row }">
                <div class="action-btns">
                  <el-button link type="primary" size="small" @click="viewTemplate(row)">查看</el-button>
                  <el-button link type="primary" size="small" @click="editTemplate(row)" :disabled="row.is_system">编辑</el-button>
                  <el-button link type="danger" size="small" @click="handleDeleteTemplate(row)" :disabled="row.is_system">删除</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </div>

      <!-- 创建/编辑模板弹窗 -->
      <el-dialog
        v-model="dialogVisible"
        :title="isEditing ? '编辑模板' : '创建模板'"
        width="640px"
        destroy-on-close
      >
        <el-form :model="tplForm" label-width="80px">
          <el-form-item label="模板名称">
            <el-input v-model="tplForm.name" placeholder="例如: my_template.md" :disabled="isEditing" />
          </el-form-item>
          <el-form-item label="领域标签">
            <el-input v-model="tplForm.domain_tag" placeholder="例如: General, NLP, CV" />
          </el-form-item>
          <el-form-item label="模板内容">
            <el-input
              v-model="tplForm.content"
              type="textarea"
              :rows="12"
              placeholder="输入 Markdown 模板内容..."
              class="mono-textarea"
            />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" @click="saveTemplate" :loading="tplSaving">保存</el-button>
        </template>
      </el-dialog>

      <!-- 查看模板弹窗 -->
      <el-dialog v-model="viewDialogVisible" :title="'查看模板 - ' + viewingTemplate.name" width="640px">
        <div class="template-meta">
          <el-tag size="small">{{ viewingTemplate.domain_tag }}</el-tag>
          <span class="template-time">更新于 {{ formatDate(viewingTemplate.updated_at) }}</span>
        </div>
        <pre class="template-content-preview">{{ viewingTemplate.content }}</pre>
      </el-dialog>
    </div>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Plus, Upload } from "@element-plus/icons-vue";
import {
  getApiKeys,
  updateApiKeys,
  listTemplates,
  getTemplate,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  uploadTemplate,
} from "../api/client";

// ---- API 密钥 ----
const loading = ref(false);
const saving = ref(false);
const form = reactive({
  deepseek_api_key: "",
  deepseek_base_url: "",
  qwen_api_key: "",
  qwen_base_url: "",
  tavily_api_key: "",
});

// 密钥配置状态：后端返回脱敏值时标记为"已配置"
const keyStatus = reactive({ deepseek: false, qwen: false, tavily: false });

const loadKeys = async () => {
  loading.value = true;
  try {
    const { data } = await getApiKeys();
    form.deepseek_base_url = data.deepseek_base_url || "";
    form.qwen_base_url = data.qwen_base_url || "";
    // 脱敏值形如 "sk-1****5678"，有值即代表已配置
    keyStatus.deepseek = !!data.deepseek_api_key;
    keyStatus.qwen = !!data.qwen_api_key;
    keyStatus.tavily = !!data.tavily_api_key;
    // 显示脱敏值让用户看到已配置
    form.deepseek_api_key = data.deepseek_api_key || "";
    form.qwen_api_key = data.qwen_api_key || "";
    form.tavily_api_key = data.tavily_api_key || "";
  } catch (error) {
    ElMessage.error("加载配置失败");
  } finally {
    loading.value = false;
  }
};

const saveKeys = async () => {
  saving.value = true;
  try {
    const payload = { ...form };
    for (const key of ["deepseek_api_key", "qwen_api_key", "tavily_api_key"]) {
      if (payload[key].includes("****") || !payload[key].trim()) {
        payload[key] = null;
      }
    }
    await updateApiKeys(payload);
    ElMessage.success("配置已保存");
    await loadKeys();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "保存失败");
  } finally {
    saving.value = false;
  }
};

const resetKeys = async () => {
  try {
    await ElMessageBox.confirm("将重新加载已保存的配置，放弃当前修改？", "重置确认", {
      confirmButtonText: "确认重置",
      cancelButtonText: "取消",
      type: "warning",
    });
    await loadKeys();
    ElMessage.success("已重置为上次保存的配置");
  } catch {
    // 用户取消
  }
};

// ---- 模板管理 ----
const tplLoading = ref(false);
const templateList = ref([]);
const dialogVisible = ref(false);
const isEditing = ref(false);
const editingId = ref(null);
const tplSaving = ref(false);
const tplForm = reactive({ name: "", content: "", domain_tag: "General" });

const viewDialogVisible = ref(false);
const viewingTemplate = reactive({ name: "", content: "", domain_tag: "", updated_at: "" });

const loadTemplates = async () => {
  tplLoading.value = true;
  try {
    const { data } = await listTemplates();
    templateList.value = data;
  } catch (error) {
    ElMessage.error("加载模板列表失败");
  } finally {
    tplLoading.value = false;
  }
};

const showCreateDialog = () => {
  isEditing.value = false;
  editingId.value = null;
  tplForm.name = "";
  tplForm.content = "";
  tplForm.domain_tag = "General";
  dialogVisible.value = true;
};

const editTemplate = async (row) => {
  try {
    const { data } = await getTemplate(row.id);
    isEditing.value = true;
    editingId.value = row.id;
    tplForm.name = data.name;
    tplForm.content = data.content;
    tplForm.domain_tag = data.domain_tag;
    dialogVisible.value = true;
  } catch (error) {
    ElMessage.error("加载模板详情失败");
  }
};

const viewTemplate = async (row) => {
  try {
    const { data } = await getTemplate(row.id);
    viewingTemplate.name = data.name;
    viewingTemplate.content = data.content;
    viewingTemplate.domain_tag = data.domain_tag;
    viewingTemplate.updated_at = data.updated_at;
    viewDialogVisible.value = true;
  } catch (error) {
    ElMessage.error("加载模板详情失败");
  }
};

const saveTemplate = async () => {
  if (!tplForm.name.trim() || !tplForm.content.trim()) {
    ElMessage.warning("模板名称和内容不能为空");
    return;
  }
  tplSaving.value = true;
  try {
    if (isEditing.value) {
      await updateTemplate(editingId.value, {
        content: tplForm.content,
        domain_tag: tplForm.domain_tag,
      });
      ElMessage.success("模板已更新");
    } else {
      await createTemplate({
        name: tplForm.name,
        content: tplForm.content,
        domain_tag: tplForm.domain_tag,
      });
      ElMessage.success("模板已创建");
    }
    dialogVisible.value = false;
    await loadTemplates();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "保存模板失败");
  } finally {
    tplSaving.value = false;
  }
};

const handleDeleteTemplate = async (row) => {
  try {
    await ElMessageBox.confirm(`确定删除模板「${row.name}」吗？`, "删除确认", {
      confirmButtonText: "删除",
      cancelButtonText: "取消",
      type: "warning",
    });
    await deleteTemplate(row.id);
    ElMessage.success("模板已删除");
    await loadTemplates();
  } catch (error) {
    if (error !== "cancel") {
      ElMessage.error(error.response?.data?.detail || "删除失败");
    }
  }
};

const beforeUploadTemplate = (file) => {
  if (!file.name.toLowerCase().endsWith(".md")) {
    ElMessage.error("仅支持上传 .md 文件");
    return false;
  }
  return true;
};

const handleUploadTemplate = async ({ file }) => {
  try {
    await uploadTemplate(file, "General");
    ElMessage.success("模板上传成功");
    await loadTemplates();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "上传失败");
  }
};

const formatDate = (dateStr) => {
  if (!dateStr) return "-";
  const d = new Date(dateStr);
  return d.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
};

onMounted(() => {
  loadKeys();
  loadTemplates();
});
</script>

<style scoped>
.settings-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
}

.settings-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.two-col-layout {
  display: flex;
  gap: 20px;
  align-items: stretch;
}

/* 左右卡片等高 */
.api-card { flex: 1; min-width: 0; }
.tpl-card { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.tpl-card .tpl-table { flex: 1; }

/* 面包屑 */
.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}

.crumb { text-decoration: none; color: #94a3b8; transition: color 0.2s; }
a.crumb:hover { color: #64748b; }
.crumb.current { color: #0f172a; font-weight: 600; font-size: 18px; }
.crumb-sep { color: #cbd5e1; font-size: 12px; }

/* 配置卡片 */
.config-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 28px 28px 24px;
}

.card-header { margin-bottom: 24px; }
.card-header h2 { margin: 0 0 6px; font-size: 17px; font-weight: 600; color: #0f172a; }
.card-desc { margin: 0; color: #64748b; font-size: 13px; line-height: 1.5; }

/* 服务商区块 */
.provider-block {
  background: #f8fafc;
  border: 1px solid #f1f5f9;
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 16px;
  transition: border-color 0.2s;
}
.provider-block:hover { border-color: #e2e8f0; }
.provider-block:last-of-type { margin-bottom: 0; }

.provider-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.provider-indicator { width: 4px; height: 22px; border-radius: 2px; }
.provider-indicator.deepseek { background: #3b82f6; }
.provider-indicator.qwen { background: #f59e0b; }
.provider-indicator.tavily { background: #10b981; }

.provider-name {
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
}

.key-status {
  margin-left: auto;
}

.provider-fields {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 模板操作按钮 */
.tpl-actions {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}

/* 保存按钮 */
.save-row { display: flex; justify-content: flex-end; gap: 12px; margin-top: 24px; }
.reset-btn { min-width: 120px; height: 42px; font-size: 14px; border-radius: 10px; }
.save-btn { min-width: 160px; height: 42px; font-size: 14px; font-weight: 600; border-radius: 10px; }

/* 操作按钮组：禁止折行 */
.action-btns {
  display: flex;
  gap: 4px;
  justify-content: center;
  white-space: nowrap;
}

/* 表格对齐 */
.tpl-table :deep(.el-table__cell) {
  padding: 8px 0;
}

/* 输入框 focus 增强 */
.provider-block :deep(.el-input__wrapper:focus-within) {
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.12);
}
.provider-block :deep(.el-form-item) { margin-bottom: 0; }
.provider-block :deep(.el-form-item__label) { font-size: 12px; color: #64748b; padding-bottom: 4px; }

/* 模板弹窗 */
.mono-textarea :deep(.el-textarea__inner) {
  font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
  font-size: 13px;
  line-height: 1.6;
}

.template-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.template-time { color: #94a3b8; font-size: 12px; }

.template-content-preview {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 16px;
  font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
  margin: 0;
}
</style>
