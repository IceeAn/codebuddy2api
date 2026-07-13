<script setup lang="ts">
import { BookOpen, ExternalLink } from '@lucide/vue';
import CCard from '../components/ui/CCard.vue';
</script>

<template>
  <CCard title="Swagger API 文档">
    <div class="flex flex-col items-start gap-5">
      <div class="grid h-12 w-12 place-items-center rounded-lg bg-brand-500/12 text-tone-brand">
        <BookOpen :size="24" />
      </div>

      <div class="max-w-2xl space-y-2">
        <p class="text-sm leading-6 text-text">
          查看服务端生成的 OpenAPI 接口定义，并通过 Swagger UI 调试外部兼容接口。
        </p>
        <p class="text-sm leading-6 text-muted">
          文档仅允许使用当前管理台登录会话访问；调用
          <code class="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-text-strong"
            >/openai/v1/*</code
          >
          时仍需在 Swagger 中填写管理台生成的 API Key。
        </p>
      </div>

      <a
        class="inline-flex h-[38px] shrink-0 items-center justify-center gap-2 rounded-md bg-primary-action px-4 text-sm font-medium whitespace-nowrap !text-white shadow-sm transition-[background-color,box-shadow,transform] duration-(--duration-fast) ease-out-quad hover:bg-primary-action-hover hover:shadow-[var(--shadow-brand-glow)] active:scale-[0.98]"
        href="/docs"
        target="_blank"
        rel="noopener noreferrer"
      >
        <ExternalLink :size="16" />
        打开 Swagger 文档
      </a>
    </div>
  </CCard>
</template>
