<template>
  <main class="login-page">
    <div class="login-card glass-panel">
      <div class="login-header">
        <div class="brand-icon">✨</div>
        <h1>Agent PaperDistiller</h1>
        <p class="subtitle">Intelligent Paper Processing Platform</p>
      </div>

      <el-tabs v-model="activeTab">
        <el-tab-pane label="登录" name="login">
          <el-form :model="loginForm" @submit.prevent="handleLogin" label-position="top">
            <el-form-item label="邮箱">
              <el-input v-model="loginForm.email" type="email" placeholder="your@email.com" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input v-model="loginForm.password" type="password" show-password placeholder="请输入密码" />
            </el-form-item>
            <el-button type="primary" :loading="loading" @click="handleLogin" style="width: 100%">
              登录
            </el-button>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="注册" name="register">
          <el-form :model="registerForm" @submit.prevent="handleRegister" label-position="top">
            <el-form-item label="邮箱">
              <el-input v-model="registerForm.email" type="email" placeholder="your@email.com" />
            </el-form-item>
            <el-form-item label="用户名">
              <el-input v-model="registerForm.username" placeholder="请输入用户名" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input v-model="registerForm.password" type="password" show-password placeholder="至少 6 位" />
            </el-form-item>
            <el-button type="primary" :loading="loading" @click="handleRegister" style="width: 100%">
              注册
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>

      <div v-if="message" :class="['login-message', messageType]">{{ message }}</div>
    </div>
  </main>
</template>

<script setup>
import { ref, reactive } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const authStore = useAuthStore();

const activeTab = ref("login");
const loading = ref(false);
const message = ref("");
const messageType = ref("info");

const loginForm = reactive({ email: "", password: "" });
const registerForm = reactive({ email: "", username: "", password: "" });

const handleLogin = async () => {
  if (!loginForm.email || !loginForm.password) {
    ElMessage.warning("请填写邮箱和密码");
    return;
  }
  loading.value = true;
  message.value = "";
  try {
    await authStore.login(loginForm.email, loginForm.password);
    ElMessage.success("登录成功");
    router.push("/dashboard");
  } catch (error) {
    const detail = error.response?.data?.detail || "登录失败";
    ElMessage.error(detail);
  } finally {
    loading.value = false;
  }
};

const handleRegister = async () => {
  if (!registerForm.email || !registerForm.username || !registerForm.password) {
    ElMessage.warning("请填写所有字段");
    return;
  }
  loading.value = true;
  message.value = "";
  try {
    await authStore.register(registerForm.email, registerForm.username, registerForm.password);
    ElMessage.success("注册成功");
    router.push("/dashboard");
  } catch (error) {
    const detail = error.response?.data?.detail || "注册失败";
    ElMessage.error(detail);
  } finally {
    loading.value = false;
  }
};
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
}

.login-card {
  width: 420px;
  padding: 32px;
  border-radius: 20px;
}

.login-header {
  text-align: center;
  margin-bottom: 24px;
}

.brand-icon {
  font-size: 32px;
  margin-bottom: 8px;
}

.login-header h1 {
  margin: 0;
  font-size: 22px;
  color: #0f172a;
}

.subtitle {
  color: #64748b;
  font-size: 13px;
  margin: 4px 0 0;
}

.login-message {
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  text-align: center;
}

.login-message.info { background: #edf6ff; color: #1d4d8f; }
.login-message.error { background: #fef2f2; color: #dc2626; }
.login-message.success { background: #f0fdf4; color: #16a34a; }
</style>
