<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { Copy, Plus, Trash2 } from '@lucide/vue';
import CAlert from '../components/ui/CAlert.vue';
import CButton from '../components/ui/CButton.vue';
import CCard from '../components/ui/CCard.vue';
import CDataTable, { type Column } from '../components/ui/CDataTable.vue';
import CInput from '../components/ui/CInput.vue';
import CInputGroup from '../components/ui/CInputGroup.vue';
import CPopconfirm from '../components/ui/CPopconfirm.vue';
import CTooltip from '../components/ui/CTooltip.vue';
import { adminApi } from '../api/admin';
import type { ApiKeyCreateResponse, ApiKeyRecord } from '../types';
import { useClipboard } from '../composables/useClipboard';
import { useToast } from '../composables/useToast';
import RefreshButton from '../components/RefreshButton.vue';
import { formatDeleteConfirm } from '../utils/apiKeyText';

const queryClient = useQueryClient();
const toast = useToast();
const { copy } = useClipboard();
const name = ref('');
const actionButtonClass = 'table-action-button';
const leaveWarning = '仍有未保存的 API Key，离开后将无法再次查看。确定要离开吗？';

interface PendingApiKey {
  id: string;
  name: string;
  apiKey: string;
  copyFailed: boolean;
  justCreated: boolean;
}

const pendingApiKeys = ref<PendingApiKey[]>([]);

function addPendingApiKey(created: ApiKeyCreateResponse): void {
  pendingApiKeys.value.push({
    id: created.id,
    name: created.name,
    apiKey: created.api_key,
    copyFailed: false,
    justCreated: true,
  });
  const pendingKey = pendingApiKeys.value[pendingApiKeys.value.length - 1];
  window.setTimeout(() => {
    pendingKey.justCreated = false;
  }, 600);
}

function confirmLeavingWithPendingKeys(): boolean {
  if (pendingApiKeys.value.length === 0) return true;
  return window.confirm(leaveWarning);
}

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  if (pendingApiKeys.value.length === 0) return;
  event.preventDefault();
  event.returnValue = leaveWarning;
}

onBeforeRouteLeave(confirmLeavingWithPendingKeys);

onMounted(() => {
  window.addEventListener('beforeunload', handleBeforeUnload);
});

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload);
});

const apiKeysQuery = useQuery({
  queryKey: ['admin-api-keys'],
  queryFn: adminApi.apiKeys,
});

const rows = computed(() => apiKeysQuery.data.value?.api_keys || []);

function formatMinuteTimestamp(value: number): string {
  return new Date(value * 1000).toLocaleString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const createMutation = useMutation({
  mutationFn: () => adminApi.createApiKey(name.value),
  onSuccess: async (created) => {
    addPendingApiKey(created);
    name.value = '';
    toast.success('API Key 已生成');
    await queryClient.invalidateQueries({ queryKey: ['admin-api-keys'] });
  },
});

const deleteMutation = useMutation({
  mutationFn: adminApi.deleteApiKey,
  onSuccess: async () => {
    toast.success('API Key 已删除');
    await queryClient.invalidateQueries({ queryKey: ['admin-api-keys'] });
  },
});

function handleCreate() {
  if (createMutation.isPending.value) return;
  if (!name.value.trim()) {
    toast.warning('请输入 API Key 名称');
    return;
  }
  createMutation.mutate();
}

function dismissNewKey(keyId: string): void {
  pendingApiKeys.value = pendingApiKeys.value.filter((pendingKey) => pendingKey.id !== keyId);
}

async function copyAndCloseNewKey(pendingKey: PendingApiKey): Promise<void> {
  const copied = await copy(pendingKey.apiKey, 'API Key 已复制');
  if (copied) {
    dismissNewKey(pendingKey.id);
    return;
  }
  pendingKey.copyFailed = true;
}

const columns: Column<ApiKeyRecord>[] = [
  { title: '名称', key: 'name', minWidth: 160 },
  { title: '预览', key: 'preview', minWidth: 180 },
  {
    title: '创建时间',
    key: 'created_at',
    minWidth: 180,
    render: (row) => new Date(row.created_at * 1000).toLocaleString(),
  },
  {
    title: '最近使用',
    key: 'last_used_at',
    minWidth: 180,
    render: (row) => (row.last_used_at ? formatMinuteTimestamp(row.last_used_at) : '-'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 96,
    align: 'left',
    headerClassName: 'table-action-header',
    render: (row) =>
      h('div', { class: 'table-action-group flex items-center justify-start gap-1.5' }, [
        h(
          CTooltip,
          { content: '删除 API Key' },
          {
            default: () =>
              h(
                CPopconfirm,
                {
                  title: formatDeleteConfirm(row.name),
                  confirmVariant: 'danger',
                  onConfirm: () => deleteMutation.mutate(row.id),
                },
                {
                  default: () =>
                    h(
                      CButton,
                      {
                        size: 'sm',
                        variant: 'secondary',
                        shape: 'circle',
                        class: actionButtonClass,
                        'aria-label': '删除 API Key',
                      },
                      { icon: () => h(Trash2, { size: 14 }) },
                    ),
                },
              ),
          },
        ),
      ]),
  },
];

// CDataTable 当前为非泛型组件，传 props 时需 cast 为其默认 Record<string, unknown> 形态
const tableColumns = columns as unknown as Column[];
const tableRows = computed(() => rows.value as unknown as Record<string, unknown>[]);
</script>

<template>
  <div class="section-grid">
    <CCard title="创建 API Key">
      <div class="flex flex-col gap-4">
        <CInputGroup>
          <CInput v-model="name" placeholder="名称" @enter="handleCreate" />
          <CButton
            variant="primary"
            :loading="createMutation.isPending.value"
            :disabled="createMutation.isPending.value"
            @click="handleCreate"
          >
            <template #icon>
              <Plus :size="16" />
            </template>
            生成
          </CButton>
        </CInputGroup>

        <CAlert
          v-for="pendingKey in pendingApiKeys"
          :key="pendingKey.id"
          type="warning"
          :class="{ 'animate-success': pendingKey.justCreated }"
        >
          <div class="mb-2">
            <strong>
              API Key「{{ pendingKey.name }}」仅显示一次，请立即复制保存，关闭后无法再次查看
            </strong>
          </div>
          <CInputGroup>
            <CInput :model-value="pendingKey.apiKey" readonly />
            <CButton v-if="!pendingKey.copyFailed" @click="copyAndCloseNewKey(pendingKey)">
              <template #icon>
                <Copy :size="16" />
              </template>
              复制并关闭
            </CButton>
            <template v-else>
              <CButton @click="copy(pendingKey.apiKey, 'API Key 已复制')">
                <template #icon>
                  <Copy :size="16" />
                </template>
                复制
              </CButton>
              <CButton variant="secondary" @click="dismissNewKey(pendingKey.id)">我已保存</CButton>
            </template>
          </CInputGroup>
        </CAlert>
      </div>
    </CCard>

    <CCard title="已创建的 API Key">
      <template #header-extra>
        <RefreshButton :query="apiKeysQuery" />
      </template>

      <CAlert v-if="apiKeysQuery.isError.value" type="error" class="mb-3">
        <div class="toolbar">
          <span>加载 API Key 列表失败</span>
          <RefreshButton :query="apiKeysQuery" label="重试" size="sm" />
        </div>
      </CAlert>

      <CDataTable
        :columns="tableColumns"
        :data="tableRows"
        :loading="apiKeysQuery.isLoading.value || apiKeysQuery.isFetching.value"
        :error="apiKeysQuery.isError.value"
        :bordered="false"
        size="small"
      >
        <template #empty>暂无 API Key，点击上方创建</template>
      </CDataTable>
    </CCard>
  </div>
</template>
