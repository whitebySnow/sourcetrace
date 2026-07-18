import { createRouter, createWebHistory } from "vue-router";

import SystemStatusPage from "@/features/system/pages/SystemStatusPage.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "system-status",
      component: SystemStatusPage,
    },
  ],
});
