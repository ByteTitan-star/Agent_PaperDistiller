<template>
  <main class="settings-page">
    <header class="settings-head">
      <el-button @click="$router.push('/dashboard')">返回总览</el-button>
      <h1>用户设置</h1>
    </header>

    <section class="settings-card glass-panel">
      <h2>API 配置</h2>
      <p class="hint">配置个人 API Key 后，系统将优先使用您的密钥。留空则使用系统默认配置。密钥经 AES 加密存储。</p>

      <el-form :model="form" label-position="top" v-loading="loading">
        <el-divider content-position="left">DeepSeek</el-divider>
        <el-form-item label="API Key">
          <el-input v-model="form.deepseek_api_key" placeholder="sk-..." show-password />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.deepseek_base_url" placeholder="https://api.deepseek.com" />
        </el-form-item>

        <el-divider content-position="left">Qwen (通义千问)</el-divider>
        <el-form-item label="API Key">
          <el-input v-model="form.qwen_api_key" placeholder="sk-..." show-password />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="form.qwen_base_url" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
        </el-form-item>

        <el-divider content-position="left">Tavily (Web Search)</el-divider>
        <el-form-item label="API Key">
          <el-input v-model="form.tavily_api_key" placeholder="tvly-..." show-password />
        </el-form-item>

        <el-button type="primary" @click="saveKeys" :loading="saving">保存配置</el-button>
      </el-form>
    </section>
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
    // masked keys are shown as-is; user can overwrite with new ones
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
    // Don't send masked keys
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
  max-width: 720px;
  margin: 0 auto;
  padding: 24px;
}

.settings-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.settings-head h1 {
  margin: 0;
  font-size: 20px;
  color: #0f172a;
}

.settings-card {
  padding: 24px;
  border-radius: 16px;
}

.settings-card h2 {
  margin: 0 0 8px;
  font-size: 16px;
  color: #0f172a;
}

.hint {
  color: #64748b;
  font-size: 13px;
  margin: 0 0 20px;
}
</style>
