<script setup lang="ts">
import { computed, h, reactive, ref, watch } from 'vue';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { Copy, ExternalLink, Pause, Play, Plus } from '@lucide/vue';
import CAlert from '../components/ui/CAlert.vue';
import CButton from '../components/ui/CButton.vue';
import CCard from '../components/ui/CCard.vue';
import CDataTable, { type Column } from '../components/ui/CDataTable.vue';
import CForm, { type FormRules } from '../components/ui/CForm.vue';
import CFormItem from '../components/ui/CFormItem.vue';
import CInput from '../components/ui/CInput.vue';
import CInputGroup from '../components/ui/CInputGroup.vue';
import CRadioGroup from '../components/ui/CRadioGroup.vue';
import CRadioButton from '../components/ui/CRadioButton.vue';
import CTag from '../components/ui/CTag.vue';
import { adminApi } from '../api/admin';
import type { CredentialRecord, CredentialsResponse } from '../types';
import { useOAuthPolling } from '../composables/useOAuthPolling';
import { useClipboard } from '../composables/useClipboard';
import { useToast } from '../composables/useToast';
import CredentialActions from '../components/CredentialActions.vue';
import RefreshButton from '../components/RefreshButton.vue';
import { filterCredentials, type CredentialFilterTab } from '../utils/credentialsFilter';

const queryClient = useQueryClient();
const toast = useToast();
const { copy } = useClipboard();

const credentialForm = reactive({ bearerToken: '', userId: '' });
const credentialFormRef = ref<InstanceType<typeof CForm> | null>(null);
const credentialRules: FormRules = {
  bearerToken: {
    required: true,
    whitespace: true,
    message: '请输入 Bearer Token',
    trigger: 'input',
  },
};

const testingId = ref<string | null>(null);

const filterTab = ref<CredentialFilterTab>('all');
const credentialFilterOrder: Record<CredentialFilterTab, number> = {
  all: 0,
  valid: 1,
  expired: 2,
};
const credentialFilterTransition = ref('credential-slide-left');

const credentialsQuery = useQuery({
  queryKey: ['admin-credentials'],
  queryFn: adminApi.credentials,
});

const allCredentials = computed<CredentialRecord[]>(
  () => credentialsQuery.data.value?.credentials || [],
);

/**
 * 凭证列表按有效性排序：可用在前、过期在后，保持同类内原相对顺序。
 * 在排序基础上按 filterTab 过滤，便于用户聚焦某类凭证。
 */
const rows = computed(() => {
  const sorted = [...allCredentials.value].sort(
    (a, b) => Number(a.is_expired) - Number(b.is_expired),
  );
  return filterCredentials(sorted, filterTab.value);
});

/**
 * 各筛选分类计数，从原始数据计算而非 filtered rows，
 * 保证切换 tab 时计数稳定。
 */
const credentialCounts = computed(() => {
  const list = allCredentials.value;
  return {
    all: list.length,
    valid: list.filter((item) => !item.is_expired).length,
    expired: list.filter((item) => item.is_expired).length,
  };
});

watch(filterTab, (current, previous) => {
  credentialFilterTransition.value =
    credentialFilterOrder[current] >= credentialFilterOrder[previous]
      ? 'credential-slide-left'
      : 'credential-slide-right';
});

const emptyDescription = computed(() => {
  const prefix =
    filterTab.value === 'valid'
      ? '暂无可用凭证'
      : filterTab.value === 'expired'
        ? '暂无过期凭证'
        : '暂无凭证';
  return credentialCounts.value.all === 0 ? `${prefix}，点击上方"开始认证"或手动添加` : prefix;
});

const currentId = computed(() => credentialsQuery.data.value?.current.credential_id || '');
const currentStatus = computed(
  () => credentialsQuery.data.value?.current.status || 'no_credentials',
);
const autoRotationSetting = computed(
  () => credentialsQuery.data.value?.current.auto_rotation_enabled,
);
const autoRotationKnown = computed(() => typeof autoRotationSetting.value === 'boolean');
const autoRotationEnabled = computed(() => autoRotationSetting.value === true);
const rotationToggleLabel = computed(() => {
  if (!autoRotationKnown.value) return '自动轮换';
  return autoRotationEnabled.value ? '关闭自动轮换' : '开启自动轮换';
});
const loadError = computed(() => credentialsQuery.error.value);
const errorMessage = computed(() => {
  const err = credentialsQuery.error.value;
  return err instanceof Error ? err.message : '未知错误';
});

/**
 * CodeBuddy OAuth 设备授权轮询。
 * 竞态、超时、组件卸载清理均由 composable 内部处理；认证成功后刷新凭证列表。
 * onSuccess 不能是 async（composable 签名为 () => void），用 void 包裹 invalidate。
 */
const { authUrl, starting, polling, elapsedSeconds, start, stop } = useOAuthPolling({
  onSuccess: () => {
    void invalidateCredentials();
  },
});

const createMutation = useMutation({
  mutationFn: () => adminApi.createCredential(credentialForm.bearerToken, credentialForm.userId),
  onSuccess: async () => {
    credentialForm.bearerToken = '';
    credentialForm.userId = '';
    credentialFormRef.value?.restoreValidation();
    toast.success('凭证已添加');
    await invalidateCredentials();
  },
});

const selectMutation = useMutation({
  mutationFn: adminApi.selectCredential,
  onSuccess: async (data) => {
    toast.success(
      data.auto_rotation_disabled_by_select ? '已切换凭证，自动轮换已关闭' : '已切换凭证',
    );
    await invalidateCredentials();
    await queryClient.invalidateQueries({ queryKey: ['admin-settings'] });
  },
});

const deleteMutation = useMutation({
  mutationFn: (credentialId: string) => adminApi.deleteCredential(credentialId),
  onSuccess: async (data, credentialId) => {
    toast.success('凭证已删除');
    // 利用返回的 current 直接更新缓存，减少一次 refetch 先用缓存数据渲染
    queryClient.setQueryData<CredentialsResponse>(['admin-credentials'], (old) => {
      if (!old) return old;
      return {
        ...old,
        credentials: old.credentials.filter((c) => c.credential_id !== credentialId),
        current: data.current,
      };
    });
    // 凭证计数变化仍 invalidate status；credentials 静默刷新确保一致性
    await queryClient.invalidateQueries({ queryKey: ['admin-status'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-credentials'] });
  },
});

/**
 * 凭证测试 mutation。
 * 通过 testingId 记录当前正在测试的 credential_id 实现按行独立 loading：
 * onMutate 置位、onSettled 清空，避免 testMutation.isPending 跨行共享。
 * onSuccess 保留业务逻辑（可用/不可用提示），错误由全局 MutationCache 处理。
 */
const testMutation = useMutation({
  mutationFn: (credentialId: string) => adminApi.testCredential(credentialId),
  onMutate: (credentialId: string) => {
    testingId.value = credentialId;
  },
  onSuccess: (result) => {
    if (result.ok) {
      toast.success('凭证可用');
    } else {
      toast.error(`测试失败：HTTP ${result.status_code}`);
    }
  },
  onSettled: () => {
    testingId.value = null;
  },
});

const toggleRotationMutation = useMutation({
  mutationFn: adminApi.toggleRotation,
  onSuccess: async (data) => {
    toast.success(data.auto_rotation_enabled ? '自动轮换已启用' : '自动轮换已暂停');
    await invalidateCredentials();
    await queryClient.invalidateQueries({ queryKey: ['admin-settings'] });
  },
});

async function invalidateCredentials() {
  await queryClient.invalidateQueries({ queryKey: ['admin-credentials'] });
  await queryClient.invalidateQueries({ queryKey: ['admin-status'] });
}

function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function reopenAuthUrl() {
  if (authUrl.value) {
    window.open(authUrl.value, '_blank', 'noopener,noreferrer');
  }
}

async function submitCredential(): Promise<void> {
  try {
    await credentialFormRef.value?.validate();
  } catch {
    return;
  }
  createMutation.mutate();
}

const columns: Column<CredentialRecord>[] = [
  {
    title: '状态',
    key: 'status',
    width: 132,
    render: (row) => {
      const active = row.credential_id === currentId.value;
      const expired = row.is_expired;
      return h(
        CTag,
        { type: active ? 'success' : expired ? 'error' : 'default' },
        { default: () => (active ? '当前' : expired ? '过期' : '可用') },
      );
    },
  },
  { title: '用户', key: 'email', minWidth: 180, render: (row) => row.email || row.user_id || '-' },
  { title: 'Token', key: 'token_preview', minWidth: 180, className: 'mono' },
  { title: '剩余', key: 'time_remaining_str', width: 120 },
  { title: '文件', key: 'filename', minWidth: 180, ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'actions',
    width: 176,
    align: 'left',
    headerClassName: 'table-action-header',
    render: (row) =>
      h(CredentialActions, {
        credential: row,
        isCurrent: row.credential_id === currentId.value,
        autoRotationEnabled: autoRotationEnabled.value,
        isTesting: testingId.value === row.credential_id,
        onSelect: (credentialId) => selectMutation.mutate(credentialId),
        onTest: (credentialId) => testMutation.mutate(credentialId),
        onDelete: (credentialId) => deleteMutation.mutate(credentialId),
      }),
  },
];

// CDataTable 当前为非泛型组件，传 props 时需 cast 为其默认 Record<string, unknown> 形态
const tableColumns = columns as unknown as Column[];
const tableRows = computed(() => rows.value as unknown as Record<string, unknown>[]);
</script>

<template>
  <div class="section-grid">
    <div
      class="grid split-grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]"
    >
      <CCard title="CodeBuddy OAuth">
        <div class="flex flex-col gap-3">
          <div class="toolbar">
            <CTag :type="polling ? 'warning' : 'default'">
              {{ polling ? '等待授权' : currentStatus }}
            </CTag>
            <CTag v-if="polling" type="warning">已等待 {{ formatElapsed(elapsedSeconds) }}</CTag>
            <CButton variant="primary" :loading="starting || polling" @click="start">
              <template #icon>
                <ExternalLink :size="16" />
              </template>
              开始认证
            </CButton>
            <CButton v-if="polling" @click="stop">取消</CButton>
          </div>
          <CAlert v-if="polling" type="info">
            请在弹出的浏览器窗口中完成授权。授权完成后将自动检测，无需手动操作。若浏览器未弹出，请点击"重新打开"按钮。
          </CAlert>
          <template v-if="authUrl">
            <CInputGroup>
              <CInput :model-value="authUrl" readonly />
              <CButton @click="copy(authUrl, '认证链接已复制')">
                <template #icon>
                  <Copy :size="16" />
                </template>
                复制
              </CButton>
            </CInputGroup>
            <div class="flex items-center gap-2">
              <span class="text-xs opacity-60">若未自动打开，请手动复制链接到浏览器打开</span>
              <CButton size="sm" @click="reopenAuthUrl">重新打开</CButton>
            </div>
          </template>
        </div>
      </CCard>

      <CCard title="手动添加">
        <CForm
          ref="credentialFormRef"
          :model="credentialForm"
          :rules="credentialRules"
          label-placement="top"
        >
          <div class="credential-manual-form-fields flex flex-col gap-0">
            <CFormItem path="bearerToken">
              <CInput
                v-model="credentialForm.bearerToken"
                type="password"
                placeholder="Bearer Token"
              />
            </CFormItem>
            <CFormItem path="userId">
              <CInput v-model="credentialForm.userId" placeholder="用户 ID" />
            </CFormItem>
            <CButton
              variant="primary"
              :loading="createMutation.isPending.value"
              @click="submitCredential"
            >
              <template #icon>
                <Plus :size="16" />
              </template>
              添加
            </CButton>
          </div>
        </CForm>
      </CCard>
    </div>

    <CCard title="凭证池">
      <template #header-extra>
        <div class="toolbar-actions">
          <CButton
            :loading="toggleRotationMutation.isPending.value"
            :disabled="!autoRotationKnown"
            @click="toggleRotationMutation.mutate()"
          >
            <template #icon>
              <Pause v-if="autoRotationEnabled" :size="16" />
              <Play v-else :size="16" />
            </template>
            {{ rotationToggleLabel }}
          </CButton>
          <RefreshButton :query="credentialsQuery" />
        </div>
      </template>

      <div v-if="loadError" class="mb-3">
        <CAlert type="error">
          <div class="flex items-center gap-2">
            <span>凭证加载失败：{{ errorMessage }}</span>
            <RefreshButton :query="credentialsQuery" label="重试" size="sm" />
          </div>
        </CAlert>
      </div>
      <CRadioGroup v-model="filterTab" class="mb-3">
        <CRadioButton value="all">全部 ({{ credentialCounts.all }})</CRadioButton>
        <CRadioButton value="valid">可用 ({{ credentialCounts.valid }})</CRadioButton>
        <CRadioButton value="expired">过期 ({{ credentialCounts.expired }})</CRadioButton>
      </CRadioGroup>
      <div class="credential-table-viewport">
        <Transition :name="credentialFilterTransition" mode="out-in">
          <div :key="filterTab" class="credential-table-pane">
            <CDataTable
              :columns="tableColumns"
              :data="tableRows"
              :loading="credentialsQuery.isLoading.value || credentialsQuery.isFetching.value"
              :error="credentialsQuery.isError.value"
              :bordered="false"
              size="small"
            >
              <template #empty>{{ emptyDescription }}</template>
            </CDataTable>
          </div>
        </Transition>
      </div>
    </CCard>
  </div>
</template>
