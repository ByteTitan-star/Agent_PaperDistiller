<template>
  <main class="login-page">
    <div class="login-card glass-panel">
      <div class="login-header">
        <div class="brand-icon">✨</div>
        <h1>Agent PaperDistiller</h1>
        <p class="subtitle">Intelligent Paper Processing Platform</p>
      </div>

      <el-tabs v-model="activeTab">
        <!-- ==================== 登录 ==================== -->
        <el-tab-pane label="登录" name="login">
          <el-form :model="loginForm" @submit.prevent="handleLogin" label-position="top">
            <el-form-item label="邮箱">
              <el-input v-model="loginForm.email" type="email" placeholder="your@email.com" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input v-model="loginForm.password" type="password" show-password placeholder="请输入密码" @keyup.enter="handleLogin" />
            </el-form-item>
            <div class="remember-row">
              <el-checkbox v-model="rememberMe">记住账号密码</el-checkbox>
            </div>
            <el-button type="primary" :loading="loading" @click="handleLogin" style="width: 100%">
              登录
            </el-button>
          </el-form>
        </el-tab-pane>

        <!-- ==================== 注册 ==================== -->
        <el-tab-pane label="注册" name="register">
          <el-steps :active="regStep - 1" :space="80" class="step-bar" finish-status="success" simple>
            <el-step title="邮箱" />
            <el-step title="验证码" />
            <el-step title="密码" />
          </el-steps>

          <!-- Step 1: 邮箱 + 用户名 -->
          <el-form v-if="regStep === 1" label-position="top" class="step-form">
            <el-form-item label="邮箱">
              <el-input v-model="regForm.email" type="email" placeholder="your@email.com" />
            </el-form-item>
            <el-form-item label="用户名">
              <el-input v-model="regForm.username" placeholder="请输入用户名" />
            </el-form-item>
            <el-button type="primary" :loading="sendingCode" @click="handleSendCode" style="width: 100%">
              发送验证码
            </el-button>
          </el-form>

          <!-- Step 2: 验证码 -->
          <el-form v-if="regStep === 2" label-position="top" class="step-form">
            <p class="step-hint">验证码已发送至 <strong>{{ regForm.email }}</strong></p>
            <el-form-item label="6 位验证码">
              <el-input v-model="regForm.code" maxlength="6" placeholder="请输入验证码" @keyup.enter="handleNextFromCode" />
            </el-form-item>
            <div class="code-actions">
              <el-button type="primary" :loading="loading" @click="handleNextFromCode" style="flex:1">下一步</el-button>
              <el-button @click="handleResendCode" :disabled="codeCooldown > 0">
                {{ codeCooldown > 0 ? `${codeCooldown}s` : '重发' }}
              </el-button>
            </div>
          </el-form>

          <!-- Step 3: 设置密码 -->
          <el-form v-if="regStep === 3" label-position="top" class="step-form">
            <el-form-item :label="`为 ${regForm.username} 设置密码`">
              <el-input v-model="regForm.password" type="password" show-password placeholder="至少 6 位" @keyup.enter="handleRegister" />
            </el-form-item>
            <el-button type="primary" :loading="loading" @click="handleRegister" style="width: 100%">
              完成注册
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </div>
  </main>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { sendCode, verifyCode, register, resendVerify } from "../api/client";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const authStore = useAuthStore();

const activeTab = ref("login");
const loading = ref(false);
const sendingCode = ref(false);
const codeCooldown = ref(0);
let cooldownTimer = null;

const loginForm = reactive({ email: "", password: "" });
const rememberMe = ref(false);

const regStep = ref(1);
const regForm = reactive({ email: "", username: "", code: "", password: "" });

onMounted(() => {
  const saved = localStorage.getItem("remembered_login");
  if (saved) {
    try {
      const { email, password } = JSON.parse(saved);
      loginForm.email = email || "";
      loginForm.password = password || "";
      rememberMe.value = true;
    } catch {}
  }
});

onBeforeUnmount(() => { if (cooldownTimer) clearInterval(cooldownTimer); });

// ---- 登录 ----
const handleLogin = async () => {
  if (!loginForm.email || !loginForm.password) { ElMessage.warning("请填写邮箱和密码"); return; }
  loading.value = true;
  try {
    await authStore.login(loginForm.email, loginForm.password);
    if (rememberMe.value) {
      localStorage.setItem("remembered_login", JSON.stringify({ email: loginForm.email, password: loginForm.password }));
    } else {
      localStorage.removeItem("remembered_login");
    }
    ElMessage.success("登录成功");
    router.push("/dashboard");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "登录失败");
  } finally { loading.value = false; }
};

// ---- Step 1: 发送验证码 ----
const handleSendCode = async () => {
  if (!regForm.email) { ElMessage.warning("请输入邮箱"); return; }
  if (!regForm.username) { ElMessage.warning("请输入用户名"); return; }
  sendingCode.value = true;
  try {
    await sendCode({ email: regForm.email, username: regForm.username });
    regStep.value = 2;
    startCooldown();
    ElMessage.success("验证码已发送");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "发送失败");
  } finally { sendingCode.value = false; }
};

// ---- Step 2: 验证码通过后直接进入密码 ----
const handleNextFromCode = async () => {
  if (!regForm.code || regForm.code.length !== 6) { ElMessage.warning("请输入 6 位验证码"); return; }
  loading.value = true;
  try {
    await verifyCode({ email: regForm.email, code: regForm.code });
    regStep.value = 3;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "验证码错误");
  } finally { loading.value = false; }
};

const handleResendCode = async () => {
  try {
    await resendVerify({ email: regForm.email });
    startCooldown();
    ElMessage.success("验证码已重新发送");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "重发失败");
  }
};

// ---- Step 3: 完成注册 ----
const handleRegister = async () => {
  if (!regForm.password || regForm.password.length < 6) { ElMessage.warning("密码至少 6 位"); return; }
  loading.value = true;
  try {
    await register({
      email: regForm.email,
      username: regForm.username,
      password: regForm.password,
      code: regForm.code,
    });
    ElMessage.success("注册成功，请登录");
    activeTab.value = "login";
    loginForm.email = regForm.email;
    loginForm.password = "";
    regStep.value = 1;
    regForm.email = ""; regForm.username = ""; regForm.code = ""; regForm.password = "";
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "注册失败");
  } finally { loading.value = false; }
};

const startCooldown = () => {
  codeCooldown.value = 60;
  if (cooldownTimer) clearInterval(cooldownTimer);
  cooldownTimer = setInterval(() => {
    codeCooldown.value--;
    if (codeCooldown.value <= 0) { clearInterval(cooldownTimer); cooldownTimer = null; }
  }, 1000);
};
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  margin-top: -80px;
}

.login-card {
  width: 440px;
  padding: 32px;
  border-radius: 20px;
}

.login-header {
  text-align: center;
  margin-bottom: 24px;
}

.brand-icon { font-size: 32px; margin-bottom: 8px; }
.login-header h1 { margin: 0; font-size: 22px; color: #0f172a; }
.subtitle { color: #64748b; font-size: 13px; margin: 4px 0 0; }

.remember-row {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.step-bar { margin-bottom: 20px; }

.step-form { padding-top: 4px; }

.step-hint {
  color: #475569;
  font-size: 13px;
  margin: 0 0 16px;
  text-align: center;
}

.step-hint strong { color: #7c3aed; }

.code-actions {
  display: flex;
  gap: 10px;
  width: 100%;
}
</style>
