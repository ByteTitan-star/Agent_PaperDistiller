// 路由
import { createRouter, createWebHistory } from "vue-router";

import HomeView from "../views/HomeView.vue";
import DashboardView from "../views/DashboardView.vue";
import WorkspaceView from "../views/WorkspaceView.vue";

// 前端路由表：`/workspace/:paperId` 会把 URL 参数注入页面 props。
const router = createRouter({
  history: createWebHistory(),
  routes: [
    // 首页：上传论文并跟踪处理进度。
    { path: "/", name: "home", component: HomeView },
    // 总览页：浏览全部论文和处理状态。
    { path: "/dashboard", name: "dashboard", component: DashboardView },
    // 工作台：读取指定论文的翻译/摘要/问答数据。
    { path: "/workspace/:paperId", name: "workspace", component: WorkspaceView, props: true }
  ]
});

export default router;
