<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { tokenizerApi, tokenizerTimeout } from '../api/tokenizer';
import { adminQueryKeys } from '../utils/adminQueryKeys';
import { useSessionStore } from '../stores/session';
import CCard from './ui/CCard.vue';
import CButton from './ui/CButton.vue';
import CInput from './ui/CInput.vue';
import CSelect from './ui/CSelect.vue';
import CAlert from './ui/CAlert.vue';
import RefreshButton from './RefreshButton.vue';

const queryClient = useQueryClient();
const key = adminQueryKeys(useSessionStore().username).tokenizers;
const query = useQuery({
  queryKey: key,
  queryFn: tokenizerApi.resources,
  networkMode: 'always',
  refetchOnReconnect: false,
});
const name = ref('');
const encoder = ref('auto');
const format = ref('hf');
const templateName = ref('');
const files = ref<File[]>([]);
const fileInput = ref<HTMLInputElement | null>(null);
const rows = ref<{ model: string; resource: string }[]>([]);
const baseline = ref('[]');
const error = ref('');
const mappingDirty = computed(() => JSON.stringify(rows.value) !== baseline.value);
const isDirty = computed(
  () =>
    mappingDirty.value ||
    files.value.length > 0 ||
    name.value !== '' ||
    templateName.value !== '' ||
    encoder.value !== 'auto' ||
    format.value !== 'hf',
);
watch(
  () => query.data.value,
  (data) => {
    if (data && !mappingDirty.value) resetMappings(data.mappings);
  },
  { immediate: true },
);
function resetMappings(mappings: Record<string, string>) {
  rows.value = Object.entries(mappings).map(([model, resource]) => ({ model, resource }));
  baseline.value = JSON.stringify(rows.value);
}
function resetUpload() {
  name.value = '';
  files.value = [];
  encoder.value = 'auto';
  format.value = 'hf';
  templateName.value = '';
  if (fileInput.value) fileInput.value.value = '';
}
function confirmDiscard(): boolean {
  if (!isDirty.value) return true;
  if (!window.confirm('当前有未保存的 Tokenizer 配置，确定放弃修改吗？')) return false;
  resetMappings(query.data.value!.mappings);
  resetUpload();
  return true;
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (!isDirty.value) return;
  event.preventDefault();
  event.returnValue = '';
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload));
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload));
defineExpose({ confirmDiscard });
const mutation = useMutation({
  mutationFn: (action: () => Promise<unknown>) => action(),
  networkMode: 'always',
  onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
});
const busy = computed(() => mutation.isPending.value);
function chooseFiles(event: Event) {
  files.value = Array.from((event.target as HTMLInputElement).files!);
}
function upload() {
  error.value = '';
  if (!name.value.trim() || !files.value.length) {
    error.value = '请输入资源名称并选择分词文件';
    return;
  }
  if (
    files.value.reduce((size, file) => size + file.size, 0) >
    query.data.value!.limits.upload_max_bytes
  ) {
    error.value = '文件总大小超过上传限制';
    return;
  }
  const form = new FormData();
  for (const file of files.value) form.append('files', file);
  form.append('name', name.value);
  form.append('encoder', encoder.value);
  form.append('format', format.value);
  form.append('template_name', templateName.value);
  mutation.mutate(async () => {
    await tokenizerApi.upload(form, tokenizerTimeout(query.data.value!));
    resetUpload();
  });
}
function saveMappings() {
  error.value = '';
  const mappings: Record<string, string> = Object.create(null);
  for (const row of rows.value) {
    const model = row.model.trim();
    if (!model || !row.resource || Object.hasOwn(mappings, model)) {
      error.value = '请填写完整且不重复的模型映射';
      return;
    }
    mappings[model] = row.resource;
  }
  mutation.mutate(async () => {
    const data = await tokenizerApi.mappings(mappings);
    resetMappings(data.mappings);
  });
}
const refreshQuery = {
  isFetching: query.isFetching,
  refetch: () => (confirmDiscard() ? query.refetch() : Promise.resolve({ isError: true })),
};
</script>

<template>
  <div class="grid gap-4">
    <CCard title="Tokenizer">
      <template #header-extra><RefreshButton :query="refreshQuery" :disabled="busy" /></template>
      <p class="text-sm text-muted">
        本地离线计数不调用
        CodeBuddy，也不代表实际账单。用户映射优先于内置默认映射；仅无模板的上传资源使用 budget_v1
        估算。
      </p>
      <CAlert v-if="query.isError.value" type="error">加载分词配置失败，请刷新重试。</CAlert>
      <CAlert v-if="error" type="error" class="mt-3">{{ error }}</CAlert>
    </CCard>
    <template v-if="query.data.value">
      <CCard title="内置资源">
        <p class="mb-3 text-sm text-muted">
          内置默认映射随系统升级。点击“创建快照”后，得到独立的用户资源，再配置映射即可固定版本。
        </p>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-sm">
            <thead>
              <tr class="border-b border-border">
                <th class="p-2">模型</th>
                <th class="p-2">来源 / 版本</th>
                <th class="p-2">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in query.data.value.builtin_resources"
                :key="item.id"
                class="border-b border-border"
              >
                <td class="p-2">{{ item.model }}</td>
                <td class="p-2">
                  <a
                    :href="`https://huggingface.co/${item.repo}/tree/${item.revision}`"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="text-brand-500"
                    >{{ item.repo }}</a
                  >
                  <div class="text-xs text-muted">
                    {{ item.revision.slice(0, 12) }} · {{ item.license }}
                  </div>
                </td>
                <td class="p-2">
                  <CButton
                    size="sm"
                    :disabled="busy"
                    @click="mutation.mutate(() => tokenizerApi.snapshot(item.id))"
                    >创建快照</CButton
                  >
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="mt-3 text-sm text-muted">
          待配置（尚无已确认的官方匹配资源）：{{ query.data.value.pending_models.join('、') }}
        </p>
      </CCard>
      <CCard title="上传用户资源">
        <fieldset :disabled="busy" class="grid gap-3">
          <label
            >资源名称<CInput v-model="name" placeholder="例如：自定义模型分词器" :disabled="busy"
          /></label>
          <div class="grid gap-3 md:grid-cols-2">
            <label
              >词表格式<CSelect
                v-model="format"
                :options="[
                  { label: 'Hugging Face tokenizer.json', value: 'hf' },
                  { label: 'Kimi tiktoken.model', value: 'kimi' },
                ]"
                :disabled="busy"
            /></label>
            <label
              >消息编码器<CSelect
                v-model="encoder"
                :options="query.data.value.encoders.map((value) => ({ label: value, value }))"
                :disabled="busy"
            /></label>
          </div>
          <label
            >模板名称（多模板配置时填写）<CInput v-model="templateName" :disabled="busy"
          /></label>
          <label
            >分词文件<input
              ref="fileInput"
              type="file"
              multiple
              class="mt-1 block w-full text-sm"
              :disabled="busy"
              @change="chooseFiles"
          /></label>
          <p class="text-sm text-muted">
            支持 tokenizer.json 或 tiktoken.model，以及
            tokenizer_config.json、chat_template.jinja、special_tokens_map.json、added_tokens.json
            和许可证。单次请求上限
            {{ query.data.value.limits.upload_max_bytes / 1048576 }} MiB（含表单开销）。不接受
            Python 脚本或模型权重。
          </p>
          <div>
            <CButton :disabled="busy" variant="primary" @click="upload">上传并验证</CButton>
          </div>
        </fieldset>
      </CCard>
      <CCard title="用户资源">
        <p v-if="!query.data.value.user_resources.length" class="text-sm text-muted">
          暂无用户资源
        </p>
        <div
          v-for="item in query.data.value.user_resources"
          :key="item.id"
          class="flex flex-wrap items-center justify-between gap-2 border-b border-border py-3"
        >
          <div>
            <strong>{{ item.name }}</strong>
            <p class="text-xs text-muted">
              {{ item.profile.encoder }} · {{ item.revision.slice(0, 12) }}
            </p>
            <p v-if="item.update_available" class="text-warning text-sm">
              内置资源已有更新；创建新快照并修改映射后才会更新此模型。
            </p>
          </div>
          <CButton
            size="sm"
            variant="danger"
            :disabled="
              busy ||
              Object.values(query.data.value.mappings).includes(item.id) ||
              rows.some((row) => row.resource === item.id)
            "
            @click="mutation.mutate(() => tokenizerApi.delete(item.id))"
            >删除</CButton
          >
        </div>
      </CCard>
      <CCard title="用户模型映射">
        <p class="mb-3 text-sm text-muted">
          精确匹配模型 ID。移除覆盖并保存后，将恢复当前内置默认映射。
        </p>
        <div
          v-for="(row, index) in rows"
          :key="index"
          class="mb-3 flex flex-wrap items-center gap-2"
        >
          <CInput
            v-model="row.model"
            placeholder="模型 ID"
            class="min-w-48 flex-1"
            :disabled="busy"
          />
          <CSelect
            v-model="row.resource"
            placeholder="选择用户资源"
            class="min-w-48 flex-1"
            :disabled="busy"
            :options="
              query.data.value.user_resources.map((item) => ({ label: item.name, value: item.id }))
            "
          />
          <CButton :disabled="busy" @click="rows.splice(index, 1)">移除</CButton>
        </div>
        <div class="flex gap-2">
          <CButton
            :disabled="busy || rows.length >= query.data.value.limits.max_mappings"
            @click="rows.push({ model: '', resource: '' })"
            >添加映射</CButton
          ><CButton variant="primary" :disabled="busy || !mappingDirty" @click="saveMappings"
            >保存映射</CButton
          >
        </div>
      </CCard>
    </template>
  </div>
</template>
