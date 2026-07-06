<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue';
import { useQuery } from '@tanstack/vue-query';
import { Play, Square } from '@lucide/vue';
import { ApiError } from '../api/client';
import { openaiPlaygroundApi } from '../api/admin';
import type { ChatCompletionRequest } from '../types';
import { SseStreamDecoder } from '../utils/sse';
import { useToast } from '../composables/useToast';
import CCard from '../components/ui/CCard.vue';
import CAlert from '../components/ui/CAlert.vue';
import CForm, { type FormRules } from '../components/ui/CForm.vue';
import CFormItem from '../components/ui/CFormItem.vue';
import CSelect from '../components/ui/CSelect.vue';
import CInput from '../components/ui/CInput.vue';
import CCheckbox from '../components/ui/CCheckbox.vue';
import CButton from '../components/ui/CButton.vue';
import RefreshButton from '../components/RefreshButton.vue';

const toast = useToast();
const selectedModel = ref('');
const prompt = ref('Hello, what is 2+2?');
const stream = ref(false);
const output = ref('点击发送查看响应');
const loading = ref(false);

// prompt 是 ref，放入 reactive 后模板校验读取到的是 unwrap 后的字符串。
const consoleForm = reactive({ prompt });
const consoleFormRef = ref<InstanceType<typeof CForm> | null>(null);
const consoleRules: FormRules = {
  prompt: { required: true, whitespace: true, message: '请输入消息', trigger: 'input' },
};

const abortController = ref<AbortController | null>(null);

const modelsQuery = useQuery({
  queryKey: ['openai-playground-models'],
  queryFn: openaiPlaygroundApi.models,
});

const modelOptions = computed(() =>
  (modelsQuery.data.value?.data || []).map((item) => ({
    label: item.id,
    value: item.id,
  })),
);

function abortInFlight(): void {
  if (abortController.value) {
    abortController.value.abort();
    abortController.value = null;
  }
}

/**
 * 流式请求使用 SseStreamDecoder 跨 chunk 累积解析；非流式请求也复用同一
 * AbortController，方便「停止」按钮和组件卸载统一中止。
 */
async function doSend(): Promise<void> {
  // 非流式请求期间禁止重复点击；流式请求点击按钮走 stop() 逻辑
  if (loading.value && !stream.value) return;

  loading.value = true;
  output.value = '';

  const requestStream = stream.value;
  const model = selectedModel.value || modelOptions.value[0]?.value || 'glm-5.2';
  const controller = new AbortController();
  abortController.value = controller;

  try {
    const response = await openaiPlaygroundApi.chat(
      {
        model,
        messages: [{ role: 'user', content: prompt.value }],
        stream: requestStream,
      } satisfies ChatCompletionRequest,
      controller.signal,
    );

    if (!response.ok) {
      output.value = await response.text();
      toast.error(`HTTP ${response.status}`);
      return;
    }

    if (!requestStream) {
      output.value = JSON.stringify(await response.json(), null, 2);
      toast.success('请求完成');
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('响应体不可读');
    }

    try {
      const decoder = new SseStreamDecoder();
      const textDecoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = textDecoder.decode(value, { stream: true });
        for (const event of decoder.feed(chunk)) {
          output.value += `${JSON.stringify(event, null, 2)}\n\n`;
        }
      }
      toast.success('流式请求完成');
    } finally {
      // 主动释放 reader，避免连接泄漏（cancel 会向服务端发送取消信号）
      try {
        await reader.cancel();
      } catch {
        // cancel 可能因连接已关闭而抛错，忽略
      }
      reader.releaseLock();
    }
  } catch (error) {
    if (controller.signal.aborted) {
      // 用户主动取消，不算错误
      output.value = '已取消';
      toast.info('请求已取消');
      return;
    }
    if (error instanceof ApiError && error.status === 401) {
      output.value = '认证过期，请重新登录';
      toast.error('认证过期，请重新登录');
      return;
    }
    output.value = error instanceof Error ? error.message : String(error);
    toast.error('请求失败');
  } finally {
    loading.value = false;
    abortController.value = null;
  }
}

async function send(): Promise<void> {
  if (loading.value && !stream.value) return;
  try {
    await consoleFormRef.value?.validate();
  } catch {
    return;
  }
  await doSend();
}

function stop(): void {
  abortInFlight();
}

onBeforeUnmount(() => {
  abortInFlight();
});
</script>

<template>
  <div class="console-layout grid grid-cols-1 gap-4">
    <CCard title="请求">
      <CForm ref="consoleFormRef" :model="consoleForm" :rules="consoleRules" label-placement="top">
        <div class="flex flex-col gap-4">
          <div class="console-model-row flex items-center gap-2">
            <CSelect
              v-model="selectedModel"
              class="min-w-0 flex-1"
              :options="modelOptions"
              :loading="modelsQuery.isLoading.value"
              placeholder="模型"
              filterable
            />
            <RefreshButton :query="modelsQuery" success-message="模型列表已刷新" />
          </div>
          <CAlert v-if="modelsQuery.isError.value" type="error" title="模型列表加载失败">
            {{ (modelsQuery.error.value as Error)?.message ?? '未知错误' }}
            <template #action>
              <RefreshButton :query="modelsQuery" label="重试" size="sm" variant="danger" />
            </template>
          </CAlert>
          <CFormItem path="prompt">
            <CInput
              v-model="prompt"
              type="textarea"
              :autosize="{ minRows: 8, maxRows: 14 }"
              placeholder="消息"
            />
          </CFormItem>
          <div class="console-action-row flex items-center justify-end gap-3">
            <CCheckbox v-model="stream" :disabled="loading">流式响应</CCheckbox>
            <CButton v-if="loading" variant="danger" @click="stop">
              <template #icon>
                <Square :size="16" />
              </template>
              停止
            </CButton>
            <CButton v-else variant="primary" :loading="loading" @click="send">
              <template #icon>
                <Play :size="16" />
              </template>
              发送
            </CButton>
          </div>
        </div>
      </CForm>
    </CCard>

    <CCard title="响应">
      <pre
        class="min-h-[20rem] overflow-auto rounded-lg bg-slate-950 p-3.5 font-mono text-[13px] leading-relaxed whitespace-pre-wrap text-slate-200"
        >{{ output }}</pre
      >
    </CCard>
  </div>
</template>
