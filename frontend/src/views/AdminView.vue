<template>
  <main class="admin-page">
    <header class="admin-head">
      <el-button @click="$router.push('/dashboard')">返回总览</el-button>
      <h1>管理员面板</h1>
    </header>

    <el-tabs v-model="activeTab">
      <!-- Token 用量 -->
      <el-tab-pane label="Token 用量" name="tokens">
        <div class="token-filters">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            size="small"
            @change="loadTokenData"
          />
          <el-select v-model="tokenPeriod" size="small" style="width: 120px; margin-left: 12px" @change="loadTokenData">
            <el-option label="按天" value="daily" />
            <el-option label="按周" value="weekly" />
            <el-option label="按月" value="monthly" />
          </el-select>
        </div>

        <div v-loading="tokenLoading" class="token-content">
          <!-- 汇总卡片 -->
          <div class="stat-cards">
            <div class="stat-card">
              <div class="stat-value">{{ formatNumber(totals.total) }}</div>
              <div class="stat-label">总 Tokens</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ formatNumber(totals.prompt) }}</div>
              <div class="stat-label">输入 Tokens</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ formatNumber(totals.completion) }}</div>
              <div class="stat-label">输出 Tokens</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ formatNumber(totals.calls) }}</div>
              <div class="stat-label">API 调用次数</div>
            </div>
          </div>

          <!-- 图表区域 -->
          <div class="chart-row">
            <div class="chart-box">
              <h3>Token 用量趋势</h3>
              <v-chart :option="trendOption" autoresize style="height: 320px" />
            </div>
            <div class="chart-box">
              <h3>按模型分布</h3>
              <v-chart :option="modelOption" autoresize style="height: 320px" />
            </div>
          </div>

          <!-- 用户排行 -->
          <div class="chart-box" style="margin-top: 16px">
            <h3>用户 Token 用量排行</h3>
            <el-table :data="tokenUsers" stripe size="small">
              <el-table-column prop="username" label="用户名" width="150" />
              <el-table-column prop="email" label="邮箱" width="220" />
              <el-table-column prop="total_tokens" label="总 Tokens" width="140">
                <template #default="{ row }">{{ formatNumber(row.total_tokens) }}</template>
              </el-table-column>
              <el-table-column prop="calls" label="调用次数" width="120" />
              <el-table-column label="操作" width="100">
                <template #default="{ row }">
                  <el-button v-if="row.user_id" size="small" @click="viewUserTokens(row)">详情</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </div>

        <!-- 用户详情弹窗 -->
        <el-dialog v-model="userDetailVisible" :title="`用户 ${userDetailName} Token 用量`" width="700px">
          <v-chart v-if="userDetailVisible" :option="userTrendOption" autoresize style="height: 300px" />
        </el-dialog>
      </el-tab-pane>

      <!-- 用户管理 -->
      <el-tab-pane label="用户管理" name="users">
        <el-table :data="users" stripe v-loading="usersLoading">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" label="用户名" width="120" />
          <el-table-column prop="email" label="邮箱" min-width="200" />
          <el-table-column prop="role" label="角色" width="80">
            <template #default="{ row }">
              <el-tag :type="row.role === 'admin' ? 'danger' : 'info'" size="small">{{ row.role === 'admin' ? '管理员' : '用户' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="is_active" label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'warning'" size="small">
                {{ row.is_active ? '活跃' : '禁用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="email_verified" label="邮箱验证" width="90">
            <template #default="{ row }">
              <el-tag :type="row.email_verified ? 'success' : 'danger'" size="small">
                {{ row.email_verified ? '已验证' : '未验证' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-dropdown trigger="click" @command="(cmd) => handleUserAction(cmd, row)">
                <el-button size="small" link type="primary">
                  操作 <el-icon style="margin-left: 4px"><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="role">
                      {{ row.role === 'admin' ? '设为用户' : '设为管理员' }}
                    </el-dropdown-item>
                    <el-dropdown-item command="status">
                      {{ row.is_active ? '禁用' : '启用' }}
                    </el-dropdown-item>
                    <el-dropdown-item command="delete" divided>
                      <span style="color: #f56c6c">删除用户</span>
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 系统配置 -->
      <el-tab-pane label="系统配置" name="settings">
        <div v-loading="settingsLoading" class="settings-grid">
          <!-- 基础信息 -->
          <el-card shadow="hover" class="config-group">
            <template #header><span class="group-title">基础信息</span></template>
            <div v-for="row in sysSettings.filter(r => ['app_name'].includes(r.setting_key))" :key="row.setting_key" class="config-row">
              <label class="config-label">{{ row.description || row.setting_key }}</label>
              <el-input v-model="row.setting_value" size="small" />
            </div>
          </el-card>

          <!-- 大模型设置 -->
          <el-card shadow="hover" class="config-group">
            <template #header><span class="group-title">大模型设置</span></template>
            <div v-for="row in sysSettings.filter(r => ['default_template'].includes(r.setting_key))" :key="row.setting_key" class="config-row">
              <label class="config-label">{{ row.description || row.setting_key }}</label>
              <el-select v-if="row.setting_key === 'default_template'" v-model="row.setting_value" size="small" style="width: 100%">
                <el-option v-for="t in templateOptions" :key="t.name" :label="`${t.name} (${t.domain_tag})`" :value="t.name" />
              </el-select>
              <el-input v-else v-model="row.setting_value" size="small" />
            </div>
          </el-card>

          <!-- Agent 检索策略 -->
          <el-card shadow="hover" class="config-group">
            <template #header><span class="group-title">Agent 检索策略</span></template>
            <div v-for="row in sysSettings.filter(r => ['react_max_rounds', 'max_chunk_chars', 'chunk_overlap'].includes(r.setting_key))" :key="row.setting_key" class="config-row">
              <label class="config-label">{{ row.description || row.setting_key }}</label>
              <el-input v-model="row.setting_value" size="small" />
            </div>
          </el-card>

          <!-- 其他配置 -->
          <el-card shadow="hover" class="config-group">
            <template #header><span class="group-title">其他配置</span></template>
            <div v-for="row in sysSettings.filter(r => !['app_name', 'default_template', 'react_max_rounds', 'max_chunk_chars', 'chunk_overlap'].includes(r.setting_key))" :key="row.setting_key" class="config-row">
              <label class="config-label">{{ row.description || row.setting_key }}</label>
              <el-switch
                v-if="isBoolField(row.setting_key)"
                :model-value="row.setting_value === 'true'"
                @change="row.setting_value = $event ? 'true' : 'false'"
                active-text="启用"
                inactive-text="关闭"
                size="small"
              />
              <el-input v-else v-model="row.setting_value" size="small" />
            </div>
          </el-card>
        </div>
        <div class="settings-save-bar">
          <el-button type="primary" @click="saveSettings" :loading="savingSettings">
            保存系统配置
          </el-button>
        </div>
      </el-tab-pane>

      <!-- 论文管理 -->
      <el-tab-pane label="论文管理" name="papers">
        <el-table :data="papers" stripe v-loading="papersLoading">
          <el-table-column prop="paper_id" label="Paper ID" width="120" show-overflow-tooltip />
          <el-table-column prop="title" label="标题" min-width="300" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusMap[row.status] || 'info'" size="small">
                {{ statusLabelMap[row.status] || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="用户" width="130">
            <template #default="{ row }">
              {{ userMap[row.user_id] || `ID: ${row.user_id}` }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button size="small" type="danger" @click="deletePaper(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 模板管理 -->
      <el-tab-pane label="模板管理" name="tplAdmin">
        <el-table :data="adminTemplates" stripe v-loading="adminTplLoading">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="name" label="模板名称" width="220" />
          <el-table-column prop="domain_tag" label="领域" width="120" />
          <el-table-column prop="owner_name" label="上传者" width="120">
            <template #default="{ row }">
              <span>{{ row.owner_name || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="owner_email" label="上传者邮箱" width="200">
            <template #default="{ row }">
              <span>{{ row.owner_email || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="is_system" label="类型" width="100">
            <template #default="{ row }">
              <el-tag :type="row.is_system ? 'success' : 'info'" size="small">
                {{ row.is_system ? '系统' : '用户' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="170">
            <template #default="{ row }">
              {{ row.created_at ? new Date(row.created_at).toLocaleString() : '-' }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button
                v-if="!row.is_system"
                size="small"
                type="danger"
                @click="adminDeleteTpl(row)"
              >删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { ArrowDown } from "@element-plus/icons-vue";
import VChart from "vue-echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { LineChart, BarChart, PieChart } from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
} from "echarts/components";
import {
  adminListUsers,
  adminChangeRole,
  adminChangeStatus,
  adminDeleteUser,
  adminGetSettings,
  adminUpdateSettings,
  adminListPapers,
  adminDeletePaper,
  adminListTemplates,
  adminDeleteTemplate,
  adminTokenOverview,
  adminTokenUsers,
  adminTokenUserDetail,
  listTemplates,
} from "../api/client";

use([CanvasRenderer, LineChart, BarChart, PieChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent]);

const activeTab = ref("tokens");
const users = ref([]);
const usersLoading = ref(false);
const sysSettings = ref([]);
const settingsLoading = ref(false);
const savingSettings = ref(false);
const papers = ref([]);
const papersLoading = ref(false);
const templateOptions = ref([]);

// Boolean 字段列表：这些字段用 Switch 组件而非 Input
const BOOL_FIELDS = new Set([
  "enable_tot", "langgraph_enabled", "rag_fallback_to_lexical",
  "agent_enable_tools", "react_enable_clarification", "oss_enabled",
  "require_email_verify", "is_public", "is_active",
]);
const isBoolField = (key) => BOOL_FIELDS.has(key);
const adminTemplates = ref([]);
const adminTplLoading = ref(false);

// Token stats state
const tokenLoading = ref(false);
const tokenPeriod = ref("daily");
const dateRange = ref(null);
const tokenOverview = ref({ totals: {}, by_model: [], by_date: [], by_action: [] });
const tokenUsers = ref([]);
const userDetailVisible = ref(false);
const userDetailName = ref("");
const userDetailData = ref([]);

const totals = computed(() => tokenOverview.value.totals || { prompt: 0, completion: 0, total: 0, calls: 0 });

const formatNumber = (n) => {
  if (n == null) return "0";
  return Number(n).toLocaleString();
};

const trendOption = computed(() => {
  const data = tokenOverview.value.by_date || [];
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: { type: "category", data: data.map((d) => d.period) },
    yAxis: { type: "value" },
    series: [
      { name: "输入", type: "bar", stack: "total", data: data.map((d) => d.prompt), itemStyle: { color: "#6366f1" } },
      { name: "输出", type: "bar", stack: "total", data: data.map((d) => d.completion), itemStyle: { color: "#22c55e" } },
      {
        name: "总量",
        type: "line",
        data: data.map((d) => d.tokens),
        smooth: true,
        lineStyle: { color: "#f59e0b", width: 2 },
        itemStyle: { color: "#f59e0b" },
      },
    ],
  };
});

const modelOption = computed(() => {
  const data = tokenOverview.value.by_model || [];
  return {
    tooltip: { trigger: "item" },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        data: data.map((d) => ({ name: d.model, value: d.tokens })),
        label: { show: true, formatter: "{b}: {d}%" },
      },
    ],
  };
});

const userTrendOption = computed(() => {
  const data = userDetailData.value;
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: { type: "category", data: data.map((d) => d.period) },
    yAxis: { type: "value" },
    series: [
      { name: "Tokens", type: "bar", data: data.map((d) => d.tokens), itemStyle: { color: "#6366f1" } },
    ],
  };
});

const loadTokenData = async () => {
  tokenLoading.value = true;
  try {
    const params = { period: tokenPeriod.value };
    if (dateRange.value && dateRange.value.length === 2) {
      params.start_date = dateRange.value[0];
      params.end_date = dateRange.value[1];
    }
    const [overviewRes, usersRes] = await Promise.all([
      adminTokenOverview(params),
      adminTokenUsers(params),
    ]);
    tokenOverview.value = overviewRes.data;
    tokenUsers.value = usersRes.data;
  } catch {
    // 数据可能为空
  } finally {
    tokenLoading.value = false;
  }
};

const viewUserTokens = async (row) => {
  userDetailName.value = row.username;
  userDetailVisible.value = true;
  try {
    const params = { period: tokenPeriod.value };
    if (dateRange.value && dateRange.value.length === 2) {
      params.start_date = dateRange.value[0];
      params.end_date = dateRange.value[1];
    }
    const { data } = await adminTokenUserDetail(row.user_id, params);
    userDetailData.value = data;
  } catch {
    userDetailData.value = [];
  }
};

const statusMap = {
  completed: "success",
  processing: "warning",
  failed: "danger",
};

const statusLabelMap = {
  completed: "已完成",
  processing: "处理中",
  failed: "失败",
};

// 用户 ID → 用户名映射
const userMap = computed(() => {
  const map = {};
  for (const u of users.value) {
    map[u.id] = u.username;
  }
  return map;
});

const loadUsers = async () => {
  usersLoading.value = true;
  try {
    const { data } = await adminListUsers();
    users.value = data;
  } finally {
    usersLoading.value = false;
  }
};

const handleUserAction = async (cmd, row) => {
  if (cmd === "role") return toggleRole(row);
  if (cmd === "status") return toggleStatus(row);
  if (cmd === "delete") return deleteUser(row);
};

const toggleRole = async (row) => {
  const newRole = row.role === "admin" ? "user" : "admin";
  await ElMessageBox.confirm(`确定将 ${row.username} 的角色改为 ${newRole}？`, "确认");
  await adminChangeRole(row.id, newRole);
  ElMessage.success("角色已更新");
  await loadUsers();
};

const toggleStatus = async (row) => {
  const action = row.is_active ? "禁用" : "启用";
  await ElMessageBox.confirm(`确定${action}用户 ${row.username}？`, "确认");
  await adminChangeStatus(row.id, !row.is_active);
  ElMessage.success("状态已更新");
  await loadUsers();
};

const loadSettings = async () => {
  settingsLoading.value = true;
  try {
    const { data } = await adminGetSettings();
    sysSettings.value = data;
  } finally {
    settingsLoading.value = false;
  }
};

const saveSettings = async () => {
  savingSettings.value = true;
  try {
    await adminUpdateSettings({ settings: sysSettings.value });
    ElMessage.success("系统配置已保存");
  } finally {
    savingSettings.value = false;
  }
};

const loadPapers = async () => {
  papersLoading.value = true;
  try {
    const { data } = await adminListPapers();
    papers.value = data;
  } finally {
    papersLoading.value = false;
  }
};

const deletePaper = async (row) => {
  await ElMessageBox.confirm(`确定删除论文 "${row.title}"？此操作不可恢复。`, "确认");
  await adminDeletePaper(row.paper_id);
  ElMessage.success("论文已删除");
  await loadPapers();
};

const deleteUser = async (row) => {
  await ElMessageBox.confirm(
    `确定删除用户「${row.username}」（${row.email}）？\n该用户的所有数据将被清除，并会发送邮件通知。此操作不可恢复。`,
    "删除用户",
    { type: "warning" }
  );
  await adminDeleteUser(row.id);
  ElMessage.success(`用户 ${row.username} 已删除，已发送邮件通知`);
  await loadUsers();
};

const loadAdminTemplates = async () => {
  adminTplLoading.value = true;
  try {
    const { data } = await adminListTemplates();
    adminTemplates.value = data;
  } finally {
    adminTplLoading.value = false;
  }
};

const adminDeleteTpl = async (row) => {
  await ElMessageBox.confirm(
    `确定删除模板「${row.name}」？${row.owner_name ? '将邮件通知上传者 ' + row.owner_name : ''}`,
    "删除模板",
    { type: "warning" }
  );
  await adminDeleteTemplate(row.id);
  ElMessage.success("模板已删除" + (row.owner_name ? "，已通知上传者" : ""));
  await loadAdminTemplates();
};

onMounted(() => {
  loadTokenData();
  loadUsers();
  loadSettings();
  loadPapers();
  loadAdminTemplates();
  listTemplates()
    .then(({ data }) => {
      templateOptions.value = Array.isArray(data) ? data : [];
    })
    .catch(() => {});
});
</script>

<style scoped>
.admin-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
}

.admin-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.admin-head h1 {
  margin: 0;
  font-size: 20px;
  color: #0f172a;
}

.token-filters {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.stat-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 20px;
  text-align: center;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #0f172a;
}

.stat-label {
  font-size: 13px;
  color: #64748b;
  margin-top: 4px;
}

.chart-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.chart-box {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 16px;
}

.chart-box h3 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #334155;
}

@media (max-width: 900px) {
  .stat-cards { grid-template-columns: repeat(2, 1fr); }
  .chart-row { grid-template-columns: 1fr; }
}

/* 系统配置卡片化 */
.settings-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}
.config-group { border-radius: 12px; }
.group-title { font-weight: 600; font-size: 14px; color: #0f172a; }
.config-row { margin-bottom: 12px; }
.config-row:last-child { margin-bottom: 0; }
.config-label { display: block; font-size: 12px; color: #64748b; margin-bottom: 4px; }
.settings-save-bar {
  position: sticky;
  bottom: 0;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(8px);
  padding: 12px 0;
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid #e2e8f0;
}
</style>
