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
        <span class="tag">生成模型 / {{ generationModel }}</span>
        <span class="tag">评估模型 / {{ evaluationModel }}</span>
        <span class="tag active-mode">多智能体模式</span> </div>

      <nav class="links">
        <RouterLink to="/" class="link">解析工作台</RouterLink> <RouterLink to="/dashboard" class="link">论文总览</RouterLink> </nav>
    </header>
    <RouterView />
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue";
import { RouterLink, RouterView } from "vue-router";
import { useSystemStore } from "./stores/system";

const systemStore = useSystemStore();
const generationModel = computed(() => systemStore.info.generation_model_name || "DeepSeek-V3");
const evaluationModel = computed(() => systemStore.info.evaluation_model_name || "Qwen3");

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
  gap: 8px;
}

.tag {
  padding: 6px 12px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.8);
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
}

.tag.active-mode {
  background: linear-gradient(to right, rgba(168, 85, 247, 0.1), rgba(236, 72, 153, 0.1));
  color: #9333ea;
  border-color: rgba(168, 85, 247, 0.2);
}

.links {
  display: flex;
  gap: 8px;
}

.link {
  text-decoration: none;
  padding: 8px 20px;
  border-radius: 100px;
  color: var(--text-muted);
  font-weight: 500;
  transition: all 0.3s ease;
}

.link:hover { color: var(--text-main); background: rgba(255, 255, 255, 0.5); }

.link.router-link-active {
  background: #ffffff;
  color: var(--text-main);
  box-shadow: 0 2px 10px rgba(0,0,0,0.05);
}
</style>