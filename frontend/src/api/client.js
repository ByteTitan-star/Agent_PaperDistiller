import axios from "axios";

// 后端服务地址：优先读取 Vite 环境变量，未配置时回退本地 8000。
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

// 统一 axios 客户端，所有请求都从这里发出。
const client = axios.create({
  baseURL: API_BASE_URL
});

// GET /api/templates -> [{ name: string }]
export const listTemplates = () => client.get("/api/templates");
// GET /api/system/info -> { app_name, model_provider, llm_model_name, ... }
export const getSystemInfo = () => client.get("/api/system/info");
// GET /api/papers -> PaperMeta[]
export const listPapers = () => client.get("/api/papers");
// GET /api/papers/{paperId} -> PaperMeta
export const getPaperById = (paperId) => client.get(`/api/papers/${paperId}`);
// GET /api/papers/{paperId}/content/{kind} -> { paper_id, kind, content }
export const getPaperContent = (paperId, kind) => client.get(`/api/papers/${paperId}/content/${kind}`);
// POST /api/papers/{paperId}/chat -> { answer, contexts[] }
export const askPaper = (paperId, payload) => client.post(`/api/papers/${paperId}/chat`, payload);
// PDF 在线预览 URL（iframe 使用）。
export const getPaperPdfUrl = (paperId) => `${API_BASE_URL}/api/papers/${paperId}/pdf`;
// PDF 下载 URL（浏览器新窗口下载）。
export const getPaperPdfDownloadUrl = (paperId) => `${API_BASE_URL}/api/papers/${paperId}/pdf/download`;
// 双栏翻译版 URL（HTML 页面）。
export const getTranslationLayoutUrl = (paperId) => `${API_BASE_URL}/api/papers/${paperId}/translation/layout`;

/**
 * 上传论文并创建异步处理任务。
 * @param {File} file - 上传的 PDF 文件。
 * @param {string} targetLanguage - 目标语言（如“中文”）。
 * @param {string} summaryTemplate - 摘要模板名（如 `tinghua.md`）。
 * @returns {Promise<import("axios").AxiosResponse<{task_id: string, paper_id: string}>>}
 */
export const uploadPaper = (file, targetLanguage, summaryTemplate) => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("target_language", targetLanguage);
  formData.append("summary_template", summaryTemplate);
  return client.post("/api/upload", formData, {
    headers: {
      "Content-Type": "multipart/form-data"
    }
  });
};
