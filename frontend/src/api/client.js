import axios from "axios";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

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

// 401 自动跳转登录
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
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
export const adminGetSettings = () => client.get("/api/admin/settings");
export const adminUpdateSettings = (data) => client.put("/api/admin/settings", data);
export const adminListPapers = () => client.get("/api/admin/papers");
export const adminDeletePaper = (paperId) => client.delete(`/api/admin/papers/${paperId}`);

// ---------------------------------------------------------------------------
// Business API
// ---------------------------------------------------------------------------
export const listTemplates = () => client.get("/api/templates");
export const getSystemInfo = () => client.get("/api/system/info");
export const listPapers = () => client.get("/api/papers");
export const getPaperById = (paperId) => client.get(`/api/papers/${paperId}`);
export const getPaperContent = (paperId, kind) => client.get(`/api/papers/${paperId}/content/${kind}`);
export const askPaper = (paperId, payload) => client.post(`/api/papers/${paperId}/chat`, payload);
export const getPaperPdfUrl = (paperId) => `${API_BASE_URL}/api/papers/${paperId}/pdf`;
export const getPaperPdfDownloadUrl = (paperId) => `${API_BASE_URL}/api/papers/${paperId}/pdf/download`;
export const getTranslationLayoutUrl = (paperId) => `${API_BASE_URL}/api/papers/${paperId}/translation/layout`;

export const uploadPaper = (file, targetLanguage, summaryTemplate) => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("target_language", targetLanguage);
  formData.append("summary_template", summaryTemplate);
  return client.post("/api/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" }
  });
};
