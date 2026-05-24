import { createRouter, createWebHistory } from "vue-router";

import HomeView from "../views/HomeView.vue";
import DashboardView from "../views/DashboardView.vue";
import WorkspaceView from "../views/WorkspaceView.vue";
import LoginView from "../views/LoginView.vue";
import SettingsView from "../views/SettingsView.vue";
import AdminView from "../views/AdminView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: LoginView, meta: { guest: true } },
    { path: "/", name: "home", component: HomeView },
    { path: "/dashboard", name: "dashboard", component: DashboardView },
    { path: "/workspace/:paperId", name: "workspace", component: WorkspaceView, props: true },
    { path: "/settings", name: "settings", component: SettingsView },
    { path: "/admin", name: "admin", component: AdminView, meta: { admin: true } },
  ],
});

// 路由守卫：未登录跳转 /login，非管理员访问 /admin 跳转 /dashboard
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem("access_token");
  const user = JSON.parse(localStorage.getItem("user") || "null");

  if (to.meta.admin && user?.role !== "admin") {
    return next("/dashboard");
  }
  if (to.meta.guest && token) {
    return next("/dashboard");
  }
  if (!to.meta.guest && !token && to.path !== "/login") {
    return next("/login");
  }
  next();
});

export default router;
