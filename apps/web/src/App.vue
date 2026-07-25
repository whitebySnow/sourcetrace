<script setup lang="ts">
import { Activity, BookOpenText } from "@lucide/vue";
import { RouterLink, RouterView } from "vue-router";
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="/" aria-label="SourceTrace 首页">
        <span class="brand-mark"
          ><BookOpenText :size="19" aria-hidden="true"
        /></span>
        <span>SourceTrace</span>
      </a>
      <nav class="topnav" aria-label="主导航">
        <RouterLink to="/">知识库</RouterLink>
        <RouterLink to="/system" title="系统状态" aria-label="系统状态">
          <Activity :size="18" aria-hidden="true" />
        </RouterLink>
      </nav>
    </header>
    <main>
      <RouterView />
    </main>
  </div>
</template>
