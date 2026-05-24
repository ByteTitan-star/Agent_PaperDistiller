<template>
  <main class="admin-page">
    <header class="admin-head">
      <el-button @click="$router.push('/dashboard')">返回总览</el-button>
      <h1>管理员面板</h1>
    </header>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="用户管理" name="users">
        <el-table :data="users" stripe v-loading="usersLoading">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" label="用户名" width="120" />
          <el-table-column prop="email" label="邮箱" width="200" />
          <el-table-column prop="role" label="角色" width="80">
            <template #default="{ row }">
              <el-tag :type="row.role === 'admin' ? 'danger' : 'info'">{{ row.role }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="is_active" label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'warning'">
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
          <el-table-column label="操作" width="180">
            <template #default="{ row }">
              <el-button size="small" @click="toggleRole(row)">
                {{ row.role === 'admin' ? '设为用户' : '设为管理员' }}
              </el-button>
              <el-button size="small" :type="row.is_active ? 'warning' : 'success'" @click="toggleStatus(row)">
                {{ row.is_active ? '禁用' : '启用' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="系统配置" name="settings">
        <el-table :data="sysSettings" stripe v-loading="settingsLoading">
          <el-table-column prop="setting_key" label="配置项" width="220" />
          <el-table-column prop="setting_value" label="值" width="200">
            <template #default="{ row }">
              <el-input v-model="row.setting_value" size="small" />
            </template>
          </el-table-column>
          <el-table-column prop="description" label="说明" />
        </el-table>
        <el-button type="primary" @click="saveSettings" :loading="savingSettings" style="margin-top: 16px">
          保存系统配置
        </el-button>
      </el-tab-pane>

      <el-tab-pane label="论文管理" name="papers">
        <el-table :data="papers" stripe v-loading="papersLoading">
          <el-table-column prop="paper_id" label="Paper ID" width="240" />
          <el-table-column prop="title" label="标题" />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusMap[row.status] || 'info'">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="user_id" label="用户ID" width="80" />
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button size="small" type="danger" @click="deletePaper(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </main>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  adminListUsers,
  adminChangeRole,
  adminChangeStatus,
  adminGetSettings,
  adminUpdateSettings,
  adminListPapers,
  adminDeletePaper,
} from "../api/client";

const activeTab = ref("users");
const users = ref([]);
const usersLoading = ref(false);
const sysSettings = ref([]);
const settingsLoading = ref(false);
const savingSettings = ref(false);
const papers = ref([]);
const papersLoading = ref(false);

const statusMap = {
  completed: "success",
  processing: "warning",
  failed: "danger",
};

const loadUsers = async () => {
  usersLoading.value = true;
  try {
    const { data } = await adminListUsers();
    users.value = data;
  } finally {
    usersLoading.value = false;
  }
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

onMounted(() => {
  loadUsers();
  loadSettings();
  loadPapers();
});
</script>

<style scoped>
.admin-page {
  max-width: 1100px;
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
</style>
