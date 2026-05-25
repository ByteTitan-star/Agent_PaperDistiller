<template>
  <div class="app-shell">
    <header class="top-nav glass-panel">
      <div class="brand-wrap">
        <div class="brand-icon">✨</div>
        <div class="brand-text">
          <div class="brand">Agent PaperDistiller</div>
          <div class="brand-desc">Intelligent Paper Processing</div>
        </div>
      </div>

      <div class="model-tags">
        <span class="tag">{{ generationModel }} / {{ evaluationModel }}</span>
        <span class="tag active-mode">Multi-Agent</span>
      </div>

      <nav class="links">
        <RouterLink to="/" class="link">解析工作台</RouterLink>
        <RouterLink to="/dashboard" class="link">论文总览</RouterLink>
        <RouterLink v-if="authStore.isLoggedIn" to="/settings" class="link">设置</RouterLink>
        <RouterLink v-if="authStore.isAdmin" to="/admin" class="link">管理</RouterLink>

        <span class="nav-divider" />

        <el-popover placement="bottom-end" :width="300" trigger="hover">
          <template #reference>
            <span class="link version-trigger">
              <el-icon :size="13"><InfoFilled /></el-icon>
            </span>
          </template>
          <div class="version-card">
            <div class="vc-header">
              <span class="vc-version">{{ systemStore.info.app_version }}</span>
              <span class="vc-date">{{ systemStore.info.app_update_date }}</span>
            </div>
            <div class="vc-author">作者：{{ systemStore.info.app_author }}</div>
            <el-divider style="margin: 8px 0" />
            <div class="vc-changelog">{{ systemStore.info.app_changelog }}</div>
          </div>
        </el-popover>

        <template v-if="authStore.isLoggedIn">
          <span class="user-name">{{ authStore.user?.username }}</span>
          <span class="logout-btn" @click="handleLogout">退出</span>
        </template>
        <RouterLink v-else to="/login" class="link">登录</RouterLink>
      </nav>
    </header>
    <RouterView />
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue";
import { RouterLink, RouterView, useRouter } from "vue-router";
import { InfoFilled } from "@element-plus/icons-vue";
import { useSystemStore } from "./stores/system";
import { useAuthStore } from "./stores/auth";

const systemStore = useSystemStore();
const authStore = useAuthStore();
const router = useRouter();
const generationModel = computed(() => systemStore.info.generation_model_name || "DeepSeek-V3");
const evaluationModel = computed(() => systemStore.info.evaluation_model_name || "Qwen3");

const handleLogout = () => {
  authStore.logout();
  router.push("/login");
};

onMounted(() => { if (!systemStore.loaded) systemStore.fetchInfo(); });
</script>

<style scoped>
.app-shell { 
  min-height: 100vh; 
  padding-top: 24px; 
  box-sizing: border-box; /* 新增这一行，确保 padding 包含在 100vh 内部 */
}


.top-nav {
  position: sticky;
  top: 24px;
  z-index: 50;
  max-width: 1232px;
  margin: 0 auto 40px auto;
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  /* 覆盖默认的玻璃圆角，使之更像胶囊 */
  border-radius: 100px; 
}

.brand-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-icon {
  font-size: 24px;
  background: linear-gradient(135deg, #a855f7, #ec4899);
  -webkit-background-clip: text;
  background-clip: text; /* 加上这行标准属性 */
  -webkit-text-fill-color: transparent;
}

.brand {
  font-family: var(--font-heading);
  font-size: 18px;
  font-weight: 600;
  color: var(--text-main);
  line-height: 1.2;
}

.brand-desc {
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.3px;
}

.model-tags {
  display: flex;
  gap: 6px;
}

.tag {
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.5);
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
}

.tag.active-mode {
  background: rgba(168, 85, 247, 0.08);
  color: #7c3aed;
}

.links {
  display: flex;
  align-items: center;
  gap: 4px;
}

.link {
  text-decoration: none;
  padding: 6px 14px;
  border-radius: 8px;
  color: var(--text-muted);
  font-weight: 500;
  font-size: 13px;
  transition: all 0.2s ease;
}

.link:hover { color: var(--text-main); background: rgba(255, 255, 255, 0.4); }

.link.router-link-active {
  color: #7c3aed;
  background: rgba(168, 85, 247, 0.06);
  font-weight: 600;
}

.nav-divider {
  width: 1px;
  height: 16px;
  background: rgba(0, 0, 0, 0.1);
  margin: 0 4px;
}

.version-trigger {
  display: inline-flex;
  align-items: center;
  color: #9333ea;
  cursor: pointer;
  padding: 6px 8px;
  border-radius: 8px;
  transition: background 0.2s;
}

.version-trigger:hover {
  background: rgba(168, 85, 247, 0.08);
}

.version-card {
  font-size: 13px;
  line-height: 1.6;
}

.vc-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.vc-version {
  font-size: 18px;
  font-weight: 700;
  color: #9333ea;
}

.vc-date {
  color: #64748b;
  font-size: 12px;
}

.vc-author {
  color: #475569;
  margin-top: 4px;
}

.vc-changelog {
  color: #64748b;
  font-size: 12px;
  line-height: 1.7;
}

.user-name {
  font-size: 13px;
  color: #334155;
  font-weight: 600;
  padding: 0 6px;
}

.logout-btn {
  font-size: 12px;
  color: #94a3b8;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
  transition: all 0.2s;
}

.logout-btn:hover {
  color: #dc2626;
  background: rgba(220, 38, 38, 0.06);
}
</style>