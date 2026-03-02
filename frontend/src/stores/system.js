import { defineStore } from "pinia";
import { getSystemInfo } from "../api/client";

// 系统信息 store：缓存后端模型配置，避免每个页面重复请求。
export const useSystemStore = defineStore("system", {
  state: () => ({
    // 默认值用于首屏占位，接口返回后会被覆盖。
    info: {
      app_name: "Personal Scholar Agent API",
      model_provider: "LocalRuleEngine",
      llm_model_name: "DemoPipeline-v1",
      generation_model_name: "DeepSeek-V3",
      evaluation_model_name: "Qwen3",
      collaboration_mode: "Multi-Agent Collaboration: DeepSeek-V3 (Gen) + Qwen3 (Eval)",
      embedding_model_name: "TokenOverlapRetriever-v1",
      pipeline_mode: "DeterministicDraft"
    },
    loaded: false
  }),
  actions: {
    // 拉取系统信息：GET /api/system/info -> info 对象。
    async fetchInfo() {
      try {
        const { data } = await getSystemInfo();
        this.info = data;
      } finally {
        this.loaded = true;
      }
    }
  }
});
