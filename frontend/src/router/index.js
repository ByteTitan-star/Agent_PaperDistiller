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

// 是否已验证过 token 有效性（带 TTL，避免过期 token 绕过守卫）
let tokenVerified = false;
let tokenVerifiedAt = 0;
const TOKEN_VERIFY_TTL_MS = 5 * 60 * 1000; // 5 分钟后重新验证

export function resetTokenVerified() {
  tokenVerified = false;
  tokenVerifiedAt = 0;
}

router.beforeEach(async (to, from, next) => {
  const token = localStorage.getItem("access_token");
  const user = JSON.parse(localStorage.getItem("user") || "null");

  // 无 token，需要登录
  if (!token) {
    tokenVerified = false;
    if (to.meta.guest || to.path === "/login") return next();
    return next("/login");
  }

  // 有 token 但未验证有效性（或验证已过期），先验证
  if (!tokenVerified || Date.now() - tokenVerifiedAt > TOKEN_VERIFY_TTL_MS) {
    try {
      const { getMe } = await import("../api/client");
      await getMe();
      tokenVerified = true;
      tokenVerifiedAt = Date.now();
    } catch {
      // token 无效，清空并跳转登录
      localStorage.removeItem("access_token");
      localStorage.removeItem("user");
      tokenVerified = false;
      tokenVerifiedAt = 0;
      return next("/login");
    }
  }

  // admin 路由权限
  if (to.meta.admin && user?.role !== "admin") {
    return next("/dashboard");
  }
  // 已登录访问登录页，重定向
  if (to.meta.guest) {
    return next("/dashboard");
  }
  next();
});

export default router;
