import { defineStore } from "pinia";
import { getPaperById, listPapers } from "../api/client";

export const usePaperStore = defineStore("papers", {
  state: () => ({
    papers: [],
    loading: false,
    total: 0,
    page: 1,
    pageSize: 12,
    totalPages: 0,
  }),
  actions: {
    async fetchPapers(page, pageSize) {
      this.loading = true;
      try {
        const p = page ?? this.page;
        const ps = pageSize ?? this.pageSize;
        const { data } = await listPapers({ page: p, page_size: ps });
        this.papers = data.items;
        this.total = data.total;
        this.page = data.page;
        this.pageSize = data.page_size;
        this.totalPages = data.total_pages;
      } finally {
        this.loading = false;
      }
    },
    async fetchPaper(paperId) {
      const { data } = await getPaperById(paperId);
      const existing = this.papers.find((item) => item.paper_id === paperId);
      if (existing) {
        Object.assign(existing, data);
      } else {
        this.papers.unshift(data);
      }
      return data;
    },
  },
});
