import { defineStore } from "pinia";
import { login as apiLogin, register as apiRegister, getMe } from "../api/client";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: localStorage.getItem("access_token") || "",
    user: JSON.parse(localStorage.getItem("user") || "null"),
  }),

  getters: {
    isLoggedIn: (state) => !!state.token,
    isAdmin: (state) => state.user?.role === "admin",
  },

  actions: {
    async login(email, password) {
      const { data } = await apiLogin({ email, password });
      this.token = data.access_token;
      this.user = data.user;
      localStorage.setItem("access_token", data.access_token);
      localStorage.setItem("user", JSON.stringify(data.user));
    },

    async register(email, username, password) {
      const { data } = await apiRegister({ email, username, password });
      this.token = data.access_token;
      this.user = data.user;
      localStorage.setItem("access_token", data.access_token);
      localStorage.setItem("user", JSON.stringify(data.user));
    },

    async fetchMe() {
      try {
        const { data } = await getMe();
        this.user = data;
        localStorage.setItem("user", JSON.stringify(data));
      } catch {
        this.logout();
      }
    },

    logout() {
      this.token = "";
      this.user = null;
      localStorage.removeItem("access_token");
      localStorage.removeItem("user");
    },
  },
});
