<script setup lang="ts">
import { BookOpenText } from "@lucide/vue";
import { RouterView } from "vue-router";
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="/" aria-label="SourceTrace 首页">
        <span class="brand-mark"><BookOpenText :size="19" aria-hidden="true" /></span>
        <span>SourceTrace</span>
      </a>
      <span class="environment">本地开发</span>
    </header>
    <main>
      <RouterView />
    </main>
  </div>
</template>
