import { createRouter, createWebHistory } from "vue-router";

import KnowledgeBaseDetailPage from "@/features/knowledge-bases/pages/KnowledgeBaseDetailPage.vue";
import KnowledgeBaseListPage from "@/features/knowledge-bases/pages/KnowledgeBaseListPage.vue";
import SystemStatusPage from "@/features/system/pages/SystemStatusPage.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "knowledge-bases",
      component: KnowledgeBaseListPage,
    },
    {
      path: "/knowledge-bases/:id",
      name: "knowledge-base-detail",
      component: KnowledgeBaseDetailPage,
    },
    {
      path: "/system",
      name: "system-status",
      component: SystemStatusPage,
    },
  ],
});
