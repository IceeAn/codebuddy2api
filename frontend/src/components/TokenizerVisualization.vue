<script setup lang="ts">
import { computed, ref } from 'vue';
import type { TokenEncoding } from '../api/tokenizer';
import { tokenColor, tokenSegments } from '../utils/tokenizerVisualization';
import CButton from './ui/CButton.vue';
import CTooltip from './ui/CTooltip.vue';

const props = defineProps<{ result: TokenEncoding }>();
const showIds = ref(false);
const segments = computed(() => tokenSegments(props.result.text, props.result.tokens));
const characters = computed(() => Array.from(props.result.text).length);
</script>

<template>
  <div class="grid gap-4">
    <div class="flex flex-wrap items-center justify-between gap-4">
      <div class="flex gap-6 text-lg font-semibold text-text-strong" aria-live="polite">
        <span>{{ result.input_tokens }} tokens</span><span>{{ characters }} 字符</span>
      </div>
      <div class="flex gap-2" aria-label="分词展示方式">
        <CButton
          :variant="showIds ? 'secondary' : 'primary'"
          :aria-pressed="!showIds"
          aria-label="显示文字"
          @click="showIds = false"
          >文字</CButton
        >
        <CButton
          :variant="showIds ? 'primary' : 'secondary'"
          :aria-pressed="showIds"
          aria-label="显示 Token ID"
          @click="showIds = true"
          >Token ID</CButton
        >
      </div>
    </div>
    <div
      v-if="showIds"
      data-testid="token-ids"
      class="flex min-h-32 flex-wrap content-start gap-1 rounded-lg border border-border p-4"
    >
      <span
        v-for="(token, index) in result.tokens"
        :key="index"
        class="rounded px-2 py-1 font-mono text-sm text-text-strong"
        :style="{ background: tokenColor(index) }"
        >{{ token.id }}</span
      >
    </div>
    <div
      v-else-if="result.text"
      data-testid="token-text"
      class="token-text min-h-32 rounded-lg border border-border p-4 text-lg leading-loose text-text-strong"
    >
      <CTooltip
        v-for="(segment, index) in segments"
        :key="index"
        class="token-segment"
        :style="{ background: segment.background }"
      >
        <template #default>{{ segment.text }}</template>
        <template #content
          ><span class="whitespace-pre-line">{{ segment.title }}</span></template
        >
      </CTooltip>
    </div>
    <p v-else class="py-8 text-center text-muted">没有可显示的文字</p>
    <p class="text-sm text-muted">
      颜色区分相邻 token。单个文字跨越多个 token 时，背景按各 token 的字节占比分色；悬停文字可查看
      Token ID。
    </p>
  </div>
</template>

<style scoped>
.token-text {
  white-space: break-spaces;
  overflow-wrap: anywhere;
}

/* 保留文本的自然换行和空白，不使用提示组件默认的 inline-flex 布局。 */
.token-segment {
  display: inline;
}
</style>
