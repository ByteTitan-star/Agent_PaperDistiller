<template>
  <main class="settings-page">
    <div class="settings-container">
      <!-- 面包屑导航 -->
      <nav class="breadcrumb">
        <RouterLink to="/dashboard" class="crumb">总览</RouterLink>
        <span class="crumb-sep">/</span>
        <span class="crumb current">用户设置</span>
      </nav>

      <!-- API 配置卡片 -->
      <section class="config-card" v-loading="loading">
        <div class="card-header">
          <h2>API 密钥配置</h2>
          <p class="card-desc">配置个人密钥后系统优先使用您的配置。留空则使用系统默认。密钥经 AES-256 加密存储。</p>
        </div>

        <!-- DeepSeek -->
        <div class="provider-block">
          <div class="provider-head">
            <span class="provider-indicator deepseek" />
            <span class="provider-name">DeepSeek</span>
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
          </div>
          <div class="provider-fields">
            <el-form-item label="API Key">
              <el-input v-model="form.tavily_api_key" placeholder="tvly-..." show-password />
            </el-form-item>
          </div>
        </div>

        <div class="save-row">
          <el-button type="primary" size="large" @click="saveKeys" :loading="saving" class="save-btn">
            保存配置
          </el-button>
        </div>
      </section>
    </div>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { getApiKeys, updateApiKeys } from "../api/client";

const loading = ref(false);
const saving = ref(false);
const form = reactive({
  deepseek_api_key: "",
  deepseek_base_url: "",
  qwen_api_key: "",
  qwen_base_url: "",
  tavily_api_key: "",
});

const loadKeys = async () => {
  loading.value = true;
  try {
    const { data } = await getApiKeys();
    form.deepseek_base_url = data.deepseek_base_url || "";
    form.qwen_base_url = data.qwen_base_url || "";
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

onMounted(loadKeys);
</script>

<style scoped>
.settings-page {
  max-width: 680px;
  margin: 0 auto;
  padding: 24px;
}

.settings-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 面包屑 */
.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}

.crumb {
  text-decoration: none;
  color: #94a3b8;
  transition: color 0.2s;
}

a.crumb:hover {
  color: #64748b;
}

.crumb.current {
  color: #0f172a;
  font-weight: 600;
  font-size: 18px;
}

.crumb-sep {
  color: #cbd5e1;
  font-size: 12px;
}

/* 配置卡片 */
.config-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  padding: 28px 28px 24px;
}

.card-header {
  margin-bottom: 28px;
}

.card-header h2 {
  margin: 0 0 6px;
  font-size: 17px;
  font-weight: 600;
  color: #0f172a;
}

.card-desc {
  margin: 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.5;
}

/* 服务商区块 */
.provider-block {
  background: #f8fafc;
  border: 1px solid #f1f5f9;
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 16px;
  transition: border-color 0.2s;
}

.provider-block:hover {
  border-color: #e2e8f0;
}

.provider-block:last-of-type {
  margin-bottom: 0;
}

.provider-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.provider-indicator {
  width: 4px;
  height: 18px;
  border-radius: 2px;
}

.provider-indicator.deepseek { background: #3b82f6; }
.provider-indicator.qwen { background: #f59e0b; }
.provider-indicator.tavily { background: #10b981; }

.provider-name {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.provider-fields {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 保存按钮 */
.save-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 24px;
}

.save-btn {
  min-width: 160px;
  height: 42px;
  font-size: 14px;
  font-weight: 600;
  border-radius: 10px;
}

/* 输入框 focus 增强 */
.provider-block :deep(.el-input__wrapper:focus-within) {
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.12);
}

.provider-block :deep(.el-form-item) {
  margin-bottom: 0;
}

.provider-block :deep(.el-form-item__label) {
  font-size: 12px;
  color: #64748b;
  padding-bottom: 4px;
}
</style>
