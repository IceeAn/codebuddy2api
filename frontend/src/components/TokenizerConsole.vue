<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useQuery } from '@tanstack/vue-query';
import { tokenizerApi, tokenizerTimeout } from '../api/tokenizer';
import { adminQueryKeys } from '../utils/adminQueryKeys';
import { useSessionStore } from '../stores/session';
import CCard from './ui/CCard.vue';
import CButton from './ui/CButton.vue';
import CInput from './ui/CInput.vue';
import CSelect from './ui/CSelect.vue';
import CAlert from './ui/CAlert.vue';
import CRadioGroup from './ui/CRadioGroup.vue';
import CRadioButton from './ui/CRadioButton.vue';
import RefreshButton from './RefreshButton.vue';

const query = useQuery({
  queryKey: adminQueryKeys(useSessionStore().username).tokenizers,
  queryFn: tokenizerApi.resources,
  networkMode: 'always',
  refetchOnReconnect: false,
});
const protocol = ref<'tokenizer' | 'anthropic'>('tokenizer');
const model = ref('');
const text = ref('你好，世界！');
const json = ref(
  JSON.stringify({ messages: [{ role: 'user', content: '你好，世界！' }] }, null, 2),
);
const output = ref('点击计数查看响应');
const busy = ref(false);
let controller: AbortController | null = null;
const models = computed(() => {
  const data = query.data.value;
  return data
    ? [
        ...new Set([
          ...data.builtin_resources.map((item) => item.model),
          ...Object.keys(data.mappings),
        ]),
      ]
        .sort()
        .map((value) => ({ label: value, value }))
    : [];
});
async function count() {
  if (busy.value) return;
  output.value = '';
  if (!navigator.onLine) {
    output.value = '当前离线，无法连接本地服务';
    return;
  }
  busy.value = true;
  controller = new AbortController();
  try {
    let body: unknown;
    if (protocol.value === 'tokenizer') {
      if (!model.value.trim()) throw new Error('请选择或输入模型 ID');
      body = { model: model.value.trim(), text: text.value };
    } else {
      const request: unknown = JSON.parse(json.value);
      if (!request || typeof request !== 'object' || Array.isArray(request))
        throw new Error('请求 JSON 必须是对象');
      body = { ...request, ...(model.value.trim() ? { model: model.value.trim() } : {}) };
    }
    const result = await tokenizerApi.count(
      protocol.value,
      body,
      tokenizerTimeout(query.data.value!),
      controller.signal,
    );
    output.value = JSON.stringify(result, null, 2);
  } catch (error) {
    output.value = (error as Error).message;
  } finally {
    busy.value = false;
    controller = null;
  }
}
onBeforeUnmount(() => controller?.abort());
</script>
<template>
  <div class="grid gap-4">
    <CCard title="Tokenizer 接口测试">
      <template #header-extra><RefreshButton :query="query" /></template>
      <div class="grid gap-4">
        <CAlert type="info"
          >计数在本地执行，不需要 CodeBuddy 凭证。template 使用模型模板；budget_v1
          为无模板资源的文本预算估算。均不代表实际上游 token 用量。</CAlert
        >
        <CAlert v-if="query.isError.value" type="error">加载本地模型映射失败，请刷新重试。</CAlert>
        <CRadioGroup
          v-model="protocol"
          class="justify-self-start"
          aria-label="计数协议"
          :disabled="busy"
          ><CRadioButton value="tokenizer">纯文本</CRadioButton
          ><CRadioButton value="anthropic">Anthropic Messages</CRadioButton></CRadioGroup
        >
        <label
          >本地映射<CSelect
            v-model="model"
            :options="models"
            placeholder="选择模型"
            filterable
            :disabled="busy"
        /></label>
        <label
          >模型 ID（可手动输入）<CInput
            v-model="model"
            placeholder="Anthropic 模式留空时使用 JSON 中的 model"
            :disabled="busy"
        /></label>
        <label v-if="protocol === 'tokenizer'"
          >文本<CInput
            v-model="text"
            type="textarea"
            :autosize="{ minRows: 6, maxRows: 16 }"
            :disabled="busy"
        /></label>
        <label v-else
          >完整 Anthropic 请求 JSON<CInput
            v-model="json"
            type="textarea"
            :autosize="{ minRows: 10, maxRows: 24 }"
            :disabled="busy"
        /></label>
        <p class="font-mono text-xs text-muted">
          {{
            protocol === 'tokenizer'
              ? '/tokenizer/v1/count_tokens'
              : '/anthropic/v1/messages/count_tokens'
          }}
        </p>
        <div>
          <CButton
            variant="primary"
            :loading="busy"
            :disabled="busy || !query.data.value"
            @click="count"
            >计数</CButton
          >
        </div>
      </div>
    </CCard>
    <CCard title="计数响应">
      <pre
        class="overflow-auto rounded-lg bg-slate-950 p-4 text-sm whitespace-pre-wrap text-slate-200"
        >{{ output }}</pre>
    </CCard>
  </div>
</template>
