import axios from "axios";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8001";

const client = axios.create({
  baseURL: API_BASE_URL
});

// ---------------------------------------------------------------------------
// JWT 拦截器：每次请求自动附带 Authorization header
// ---------------------------------------------------------------------------
client.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 401/403 自动跳转登录
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || "";
    // 401 = token 无效/过期；403 + "Not authenticated" = FastAPI HTTPBearer 拒绝
    if (status === 401 || (status === 403 && detail === "Not authenticated")) {
      localStorage.removeItem("access_token");
      localStorage.removeItem("user");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// ---------------------------------------------------------------------------
// Auth API
// ---------------------------------------------------------------------------
export const sendCode = (data) => client.post("/api/auth/send-code", data);
export const verifyCode = (data) => client.post("/api/auth/verify-code", data);
export const register = (data) => client.post("/api/auth/register", data);
export const login = (data) => client.post("/api/auth/login", data);
export const getMe = () => client.get("/api/auth/me");
export const resendVerify = (data) => client.post("/api/auth/resend-verify", data);
export const forgotPassword = (data) => client.post("/api/auth/forgot-password", data);
export const resetPassword = (data) => client.post("/api/auth/reset-password", data);

// ---------------------------------------------------------------------------
// Settings API
// ---------------------------------------------------------------------------
export const getApiKeys = () => client.get("/api/settings/api-keys");
export const updateApiKeys = (data) => client.put("/api/settings/api-keys", data);

// Admin
export const adminListUsers = () => client.get("/api/auth/users");
export const adminChangeRole = (userId, role) => client.put(`/api/auth/users/${userId}/role`, null, { params: { role } });
export const adminChangeStatus = (userId, is_active) => client.put(`/api/auth/users/${userId}/status`, null, { params: { is_active } });
export const adminDeleteUser = (userId) => client.delete(`/api/auth/users/${userId}`);
export const adminGetSettings = () => client.get("/api/admin/settings");
export const adminUpdateSettings = (data) => client.put("/api/admin/settings", data);
export const adminListPapers = () => client.get("/api/admin/papers");
export const adminDeletePaper = (paperId) => client.delete(`/api/admin/papers/${paperId}`);
export const adminListTemplates = () => client.get("/api/admin/templates");
export const adminDeleteTemplate = (templateId) => client.delete(`/api/admin/templates/${templateId}`);

// Token Stats
export const adminTokenOverview = (params) => client.get("/api/admin/token-stats/overview", { params });
export const adminTokenUsers = (params) => client.get("/api/admin/token-stats/users", { params });
export const adminTokenUserDetail = (userId, params) => client.get(`/api/admin/token-stats/users/${userId}`, { params });

// ---------------------------------------------------------------------------
// Business API
// ---------------------------------------------------------------------------
export const listTemplates = () => client.get("/api/templates");
export const getTemplate = (id) => client.get(`/api/templates/${id}`);
export const createTemplate = (data) => client.post("/api/templates", data);
export const updateTemplate = (id, data) => client.put(`/api/templates/${id}`, data);
export const deleteTemplate = (id) => client.delete(`/api/templates/${id}`);
export const uploadTemplate = (file, domainTag = "General") => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("domain_tag", domainTag);
  return client.post("/api/templates/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
};
export const getSystemInfo = () => client.get("/api/system/info");
export const listPapers = (params) => client.get("/api/papers", { params });
export const deletePaper = (paperId) => client.delete(`/api/papers/${paperId}`);
export const getPaperById = (paperId) => client.get(`/api/papers/${paperId}`);
export const getPaperContent = (paperId, kind) => client.get(`/api/papers/${paperId}/content/${kind}`);
export const askPaper = (paperId, payload) => client.post(`/api/papers/${paperId}/chat`, payload);

// Chat History API
export const listChatSessions = (paperId) => client.get(`/api/papers/${paperId}/chat/sessions`);
export const getChatMessages = (sessionId) => client.get(`/api/chat/sessions/${sessionId}/messages`);
export const deleteChatSession = (sessionId) => client.delete(`/api/chat/sessions/${sessionId}`);

/**
 * 流式问答 — 返回 ReadableStream，通过 onToken 回调逐 token 接收。
 * @param {string} paperId
 * @param {object} payload - { question, top_k, deep_search }
 * @param {function} onEvent - (event: {type, ...}) => void
 * @returns {Promise<void>} 流结束后 resolve
 */
export const askPaperStream = async (paperId, payload, onEvent) => {
  const token = localStorage.getItem("access_token");
  const resp = await fetch(`${API_BASE_URL}/api/papers/${paperId}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: "请求失败" }));
    throw { response: { data: err } };
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // 保留未完成的行
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          const data = JSON.parse(line.slice(6));
          onEvent(data);
        } catch {}
      }
    }
  }
  // 处理剩余 buffer
  if (buffer.startsWith("data: ")) {
    try {
      const data = JSON.parse(buffer.slice(6));
      onEvent(data);
    } catch {}
  }
};
export const getPaperPdfUrl = (paperId) => {
  const token = localStorage.getItem("access_token");
  return `${API_BASE_URL}/api/papers/${paperId}/pdf?token=${token}`;
};
export const getPaperPdfDownloadUrl = (paperId) => {
  const token = localStorage.getItem("access_token");
  return `${API_BASE_URL}/api/papers/${paperId}/pdf/download?token=${token}`;
};
export const getTranslationLayoutUrl = (paperId) => {
  const token = localStorage.getItem("access_token");
  return `${API_BASE_URL}/api/papers/${paperId}/translation/layout?token=${token}`;
};

export const uploadPaper = (file, targetLanguage, summaryTemplate) => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("target_language", targetLanguage);
  formData.append("summary_template", summaryTemplate);
  return client.post("/api/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" }
  });
};
