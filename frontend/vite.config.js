import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  // 启用 Vue SFC 编译插件。
  plugins: [vue()],
  server: {
    // 前端开发服务器端口。
    port: 5173
  }
});
