<script setup lang="ts">
import { AlertTriangle, CheckCircle2, RefreshCw, ServerCog } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";

import { getReadiness } from "../api/system";

type Readiness = Awaited<ReturnType<typeof getReadiness>>;

const readiness = ref<Readiness>();
const loading = ref(false);
const errorMessage = ref("");

const checks = computed(() => Object.entries(readiness.value?.checks ?? {}));

async function refresh() {
  loading.value = true;
  errorMessage.value = "";
  try {
    readiness.value = await getReadiness();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "无法读取服务状态。";
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <section class="status-page" aria-labelledby="page-title">
    <div class="page-heading">
      <div>
        <p class="eyebrow">开发环境</p>
        <h1 id="page-title">系统状态</h1>
        <p class="description">API、数据库与任务队列的连接状态。</p>
      </div>
      <button class="icon-button" type="button" :disabled="loading" title="刷新状态" @click="refresh">
        <RefreshCw :size="18" :class="{ spinning: loading }" aria-hidden="true" />
        <span class="sr-only">刷新状态</span>
      </button>
    </div>

    <div v-if="errorMessage" class="message error-message" role="alert">
      <AlertTriangle :size="19" aria-hidden="true" />
      <span>{{ errorMessage }}</span>
    </div>

    <div v-else-if="loading && !readiness" class="message" aria-live="polite">
      <RefreshCw :size="19" class="spinning" aria-hidden="true" />
      <span>正在检查服务状态...</span>
    </div>

    <div v-else-if="readiness" class="status-panel">
      <div class="summary-row">
        <div class="summary-icon" :class="readiness.status">
          <CheckCircle2 v-if="readiness.status === 'ok'" :size="21" aria-hidden="true" />
          <ServerCog v-else :size="21" aria-hidden="true" />
        </div>
        <div>
          <h2>{{ readiness.status === "ok" ? "全部服务可用" : "部分依赖尚未就绪" }}</h2>
          <p>API 已响应，依赖状态如下。</p>
        </div>
        <span class="status-label" :class="readiness.status">
          {{ readiness.status === "ok" ? "正常" : "降级" }}
        </span>
      </div>

      <div class="checks" role="list">
        <div v-for="[name, status] in checks" :key="name" class="check-row" role="listitem">
          <span class="check-name">{{ name }}</span>
          <span class="check-value" :class="status">
            {{ status === "ok" ? "已连接" : "未配置" }}
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.status-page {
  width: min(760px, calc(100% - 40px));
  margin: 0 auto;
  padding: 72px 0;
}

.page-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 28px;
}

.eyebrow {
  margin: 0 0 8px;
  color: #1f6f4a;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

h1,
h2,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 8px;
  font-size: 30px;
  line-height: 1.2;
  letter-spacing: 0;
}

.description,
.summary-row p {
  margin-bottom: 0;
  color: #68716b;
  line-height: 1.6;
}

.icon-button {
  display: grid;
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  place-items: center;
  color: #435048;
  background: #ffffff;
  border: 1px solid #d6ddd8;
  border-radius: 6px;
  cursor: pointer;
}

.icon-button:hover:not(:disabled) {
  background: #eef3ef;
  border-color: #aebbb2;
}

.icon-button:focus-visible {
  outline: 3px solid #9bc8ad;
  outline-offset: 2px;
}

.icon-button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.status-panel {
  overflow: hidden;
  background: #ffffff;
  border: 1px solid #dfe5e0;
  border-radius: 8px;
  box-shadow: 0 8px 22px rgb(30 51 39 / 6%);
}

.summary-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  padding: 24px;
  border-bottom: 1px solid #e5e9e6;
}

.summary-row h2 {
  margin-bottom: 5px;
  font-size: 17px;
  line-height: 1.35;
  letter-spacing: 0;
}

.summary-icon {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  color: #745718;
  background: #fff3d4;
  border-radius: 6px;
}

.summary-icon.ok {
  color: #12663f;
  background: #dff4e7;
}

.status-label,
.check-value {
  font-size: 13px;
  font-weight: 700;
}

.status-label {
  color: #745718;
}

.status-label.ok,
.check-value.ok {
  color: #12663f;
}

.checks {
  padding: 8px 24px;
}

.check-row {
  display: flex;
  min-height: 52px;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #edf0ee;
}

.check-row:last-child {
  border-bottom: 0;
}

.check-name {
  color: #39443d;
  font-size: 14px;
  text-transform: capitalize;
}

.check-value.not_configured {
  color: #8a6316;
}

.message {
  display: flex;
  min-height: 76px;
  align-items: center;
  gap: 12px;
  padding: 20px;
  color: #4c5750;
  background: #ffffff;
  border: 1px solid #dfe5e0;
  border-radius: 8px;
}

.error-message {
  color: #8d2929;
  background: #fff7f6;
  border-color: #eac7c3;
}

.spinning {
  animation: spin 0.9s linear infinite;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .status-page {
    width: min(100% - 32px, 760px);
    padding: 44px 0;
  }

  .summary-row {
    grid-template-columns: 44px minmax(0, 1fr);
    padding: 20px;
  }

  .status-label {
    grid-column: 2;
  }

  .checks {
    padding: 6px 20px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
