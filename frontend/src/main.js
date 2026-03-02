import { createApp } from "vue";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";

import App from "./App.vue";
import router from "./router";
import "./style.css";

// 前端应用启动顺序：创建 Vue 实例 -> 注入状态管理/路由/UI 组件库 -> 挂载到 #app。
const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(ElementPlus);
app.mount("#app");
