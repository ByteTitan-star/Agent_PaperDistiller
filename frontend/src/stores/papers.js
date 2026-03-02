// 状态管理（数据流）
import { defineStore } from "pinia";
import { getPaperById, listPapers } from "../api/client";

// 论文 store：统一管理论文列表与单篇详情缓存。
export const usePaperStore = defineStore("papers", {
  state: () => ({
    papers: [],
    loading: false
  }),
  actions: {
    // 拉取论文列表：GET /api/papers -> PaperMeta[]。
    async fetchPapers() {
      this.loading = true;
      try {
        const { data } = await listPapers();
        this.papers = data;
      } finally {
        this.loading = false;
      }
    },
    // 拉取单篇论文：GET /api/papers/{paperId}，并回写到列表缓存中。
    async fetchPaper(paperId) {
      const { data } = await getPaperById(paperId);
      const existing = this.papers.find((item) => item.paper_id === paperId);
      if (existing) {
        Object.assign(existing, data);
      } else {
        this.papers.unshift(data);
      }
      return data;
    }
  }
});
