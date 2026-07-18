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
import CRadioGroup from '../components/ui/CRadioGroup.vue';
import CRadioButton from '../components/ui/CRadioButton.vue';
import CTag from '../components/ui/CTag.vue';
import { adminApi } from '../api/admin';
import type { CredentialRecord, CredentialsResponse } from '../types';
import { useOAuthPolling } from '../composables/useOAuthPolling';
import { useClipboard } from '../composables/useClipboard';
import { useToast } from '../composables/useToast';
import CredentialActions from '../components/CredentialActions.vue';
import CredentialAccountSwitcher from '../components/CredentialAccountSwitcher.vue';
import CredentialQuotaRing from '../components/CredentialQuotaRing.vue';
import RefreshButton from '../components/RefreshButton.vue';
import { filterCredentials, type CredentialFilterTab } from '../utils/credentialsFilter';
import { useSessionStore } from '../stores/session';
import { adminQueryKeys } from '../utils/adminQueryKeys';

const queryClient = useQueryClient();
const session = useSessionStore();
const queryKeys = adminQueryKeys(session.username);
const toast = useToast();
const { copy } = useClipboard();

const credentialForm = reactive({ bearerToken: '' });
const credentialFormRef = ref<InstanceType<typeof CForm> | null>(null);
const credentialRules: FormRules = {
  bearerToken: {
    required: true,
    whitespace: true,
    message: '请输入 Bearer Token',
    trigger: 'input',
  },
};

const testingIds = reactive(new Set<string>());
const selectingId = ref<string | null>(null);
const deletingId = ref<string | null>(null);
const accountSwitcherCredentialId = ref('');
const accountSwitching = ref(false);

const filterTab = ref<CredentialFilterTab>('all');
const credentialFilterOrder: Record<CredentialFilterTab, number> = {
  all: 0,
  valid: 1,
  expired: 2,
};
const credentialFilterTransition = ref('credential-slide-left');

const credentialsQuery = useQuery({
  queryKey: queryKeys.credentials,
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
const {
  authUrl,
  starting,
  polling,
  elapsedSeconds,
  manualOpenRequired,
  start: startOAuth,
  cancel,
  openAuthUrl,
} = useOAuthPolling({
  onSuccess: () => {
    void invalidateCredentials();
  },
});
const authInProgress = computed(() => starting.value || polling.value);

const createMutation = useMutation({
  mutationFn: () => adminApi.createCredential(credentialForm.bearerToken),
  onSuccess: async () => {
    credentialForm.bearerToken = '';
    credentialFormRef.value?.restoreValidation();
    toast.success('凭证已添加');
    await invalidateCredentials();
  },
});

const selectMutation = useMutation({
  mutationFn: adminApi.selectCredential,
  onMutate: (credentialId: string) => {
    selectingId.value = credentialId;
  },
  onSuccess: async (data) => {
    toast.success(
      data.auto_rotation_disabled_by_select ? '已切换凭证，自动轮换已关闭' : '已切换凭证',
    );
    await invalidateCredentials();
    await queryClient.invalidateQueries({ queryKey: queryKeys.settings });
  },
  onSettled: (_data, _error, credentialId) => {
    if (selectingId.value === credentialId) selectingId.value = null;
  },
});

const deleteMutation = useMutation({
  mutationFn: (credentialId: string) => adminApi.deleteCredential(credentialId),
  onMutate: (credentialId: string) => {
    deletingId.value = credentialId;
  },
  onSuccess: async (data, credentialId) => {
    toast.success('凭证已删除');
    // 利用返回的 current 直接更新缓存，减少一次 refetch 先用缓存数据渲染
    queryClient.setQueryData<CredentialsResponse>(queryKeys.credentials, (old) => {
      if (!old) return old;
      return {
        ...old,
        credentials: old.credentials.filter((c) => c.credential_id !== credentialId),
        current: data.current,
      };
    });
    // 凭证计数变化仍 invalidate status；credentials 静默刷新确保一致性
    await queryClient.invalidateQueries({ queryKey: queryKeys.status });
    await queryClient.invalidateQueries({ queryKey: queryKeys.credentials });
  },
  onSettled: (_data, _error, credentialId) => {
    if (deletingId.value === credentialId) deletingId.value = null;
  },
});

/**
 * 凭证测试 mutation。
 * 通过 testingIds 记录全部正在测试的 credential_id，实现不同凭证并行测试，
 * 同时避免先完成的请求清掉其他仍在进行的行状态。
 * onSuccess 保留业务逻辑（可用/不可用提示），错误由全局 MutationCache 处理。
 */
const testMutation = useMutation({
  mutationFn: (credentialId: string) => adminApi.testCredential(credentialId),
  onMutate: (credentialId: string) => {
    testingIds.add(credentialId);
  },
  onSuccess: (result) => {
    if (result.ok) {
      if (result.model_source === 'configured_fallback') {
        toast.warning('凭证可用（使用本地配置模型回退）');
        return;
      }
      toast.success('凭证可用');
    } else {
      toast.error(`测试失败：${result.detail || `HTTP ${result.status_code}`}`);
    }
  },
  onSettled: (_data, _error, credentialId) => {
    testingIds.delete(credentialId);
  },
});

const toggleRotationMutation = useMutation({
  mutationFn: adminApi.toggleRotation,
  onSuccess: async (data) => {
    toast.success(data.auto_rotation_enabled ? '自动轮换已启用' : '自动轮换已暂停');
    await invalidateCredentials();
    await queryClient.invalidateQueries({ queryKey: queryKeys.settings });
  },
});

const hasActiveTests = computed(() => testingIds.size > 0);
const writeInProgress = computed(
  () =>
    selectingId.value !== null ||
    deletingId.value !== null ||
    createMutation.isPending.value ||
    toggleRotationMutation.isPending.value ||
    accountSwitching.value,
);

async function invalidateCredentials() {
  await queryClient.invalidateQueries({ queryKey: queryKeys.credentials });
  await queryClient.invalidateQueries({ queryKey: queryKeys.status });
}

function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

async function submitCredential(): Promise<void> {
  if (writeInProgress.value || hasActiveTests.value) return;
  try {
    await credentialFormRef.value?.validate();
  } catch {
    return;
  }
  createMutation.mutate();
}

function start(): void {
  if (writeInProgress.value || hasActiveTests.value) return;
  void startOAuth();
}

function selectCredential(credentialId: string): void {
  if (writeInProgress.value || hasActiveTests.value) return;
  selectMutation.mutate(credentialId);
}

function testCredential(credentialId: string): void {
  if (writeInProgress.value || testingIds.has(credentialId)) return;
  testMutation.mutate(credentialId);
}

function deleteCredential(credentialId: string): void {
  if (writeInProgress.value || hasActiveTests.value) return;
  deleteMutation.mutate(credentialId);
}

function toggleRotation(): void {
  if (!autoRotationKnown.value || writeInProgress.value || hasActiveTests.value) return;
  toggleRotationMutation.mutate();
}

function openAccountSwitcher(credentialId: string): void {
  if (writeInProgress.value || hasActiveTests.value) return;
  accountSwitcherCredentialId.value = credentialId;
}

function closeAccountSwitcher(): void {
  if (!accountSwitching.value) accountSwitcherCredentialId.value = '';
}

const columns: Column<CredentialRecord>[] = [
  {
    title: '状态',
    key: 'status',
    width: 132,
    render: (row) => {
      const active = row.credential_id === currentId.value;
      const expired = row.is_expired;
      const activeExpired = active && expired;
      return h(
        CTag,
        { type: activeExpired || expired ? 'error' : active ? 'success' : 'default' },
        {
          default: () =>
            activeExpired ? '当前 · 已过期' : active ? '当前' : expired ? '过期' : '可用',
        },
      );
    },
  },
  {
    title: '用户',
    key: 'email',
    minWidth: 220,
    render: (row) => {
      const identity = row.nickname || row.preferred_username || row.email || row.user_id || '-';
      return row.enterprise_name ? `${identity} · ${row.enterprise_name}` : identity;
    },
  },
  { title: 'Token', key: 'token_display', minWidth: 180, className: 'mono' },
  { title: '剩余', key: 'time_remaining_str', width: 120 },
  {
    title: '额度',
    key: 'quota',
    width: 84,
    align: 'center',
    render: (row) => (row.quota ? h(CredentialQuotaRing, { quota: row.quota }) : '-'),
  },
  { title: '文件', key: 'filename', minWidth: 180, ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'actions',
    width: 216,
    align: 'left',
    headerClassName: 'table-action-header',
    render: (row) =>
      h(CredentialActions, {
        credential: row,
        isCurrent: row.credential_id === currentId.value,
        autoRotationEnabled: autoRotationEnabled.value,
        isTesting: testingIds.has(row.credential_id),
        isSelecting: selectingId.value === row.credential_id,
        isDeleting: deletingId.value === row.credential_id,
        writeInProgress: writeInProgress.value,
        hasActiveTests: hasActiveTests.value,
        canSwitchAccount: Boolean(row.has_refresh_token && (row.account_count || 0) > 1),
        onSelect: selectCredential,
        onTest: testCredential,
        onDelete: deleteCredential,
        onSwitchAccount: openAccountSwitcher,
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
      <CCard title="CodeBuddy 登录认证" class="credential-auth-card">
        <div class="credential-auth-card-content flex flex-col gap-3">
          <div v-if="!authInProgress" class="credential-auth-idle flex flex-col gap-3">
            <p class="text-sm opacity-70">
              登录 CodeBuddy 后，认证凭证会自动保存到当前管理用户的凭证池。
            </p>
            <CButton
              variant="primary"
              :loading="starting || polling"
              :disabled="writeInProgress || hasActiveTests"
              class="credential-auth-start-button"
              @click="start"
            >
              <template #icon>
                <ExternalLink :size="16" />
              </template>
              开始认证
            </CButton>
          </div>
          <div v-else class="credential-auth-pending flex flex-col gap-3">
            <div class="flex flex-wrap items-center gap-2">
              <CTag type="warning">等待登录认证</CTag>
              <CTag type="warning">已等待 {{ formatElapsed(elapsedSeconds) }}</CTag>
            </div>
            <CAlert type="info">
              <template v-if="manualOpenRequired && authUrl">
                登录页未能自动打开，请点击“打开登录页”继续认证。
              </template>
              <template v-else>
                请在打开的 CodeBuddy 登录页中完成认证，完成后此处会自动更新。
              </template>
            </CAlert>
            <div class="flex flex-wrap items-center gap-2">
              <CButton :disabled="!authUrl" @click="openAuthUrl">
                <template #icon>
                  <ExternalLink :size="16" />
                </template>
                打开登录页
              </CButton>
              <CButton :disabled="!authUrl" @click="copy(authUrl, '认证链接已复制')">
                <template #icon>
                  <Copy :size="16" />
                </template>
                复制链接
              </CButton>
              <CButton @click="cancel">取消认证</CButton>
            </div>
          </div>
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
            <CButton
              variant="primary"
              :loading="createMutation.isPending.value"
              :disabled="writeInProgress || hasActiveTests"
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
            :disabled="!autoRotationKnown || writeInProgress || hasActiveTests"
            @click="toggleRotation"
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
              row-key="credential_id"
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
    <CredentialAccountSwitcher
      :open="Boolean(accountSwitcherCredentialId)"
      :credential-id="accountSwitcherCredentialId"
      @close="closeAccountSwitcher"
      @switching="accountSwitching = $event"
    />
  </div>
</template>

<style scoped>
@media (min-width: 1024px) {
  .credential-auth-card :deep(.c-card-body),
  .credential-auth-card-content,
  .credential-auth-idle {
    display: flex;
    flex: 1;
    flex-direction: column;
  }

  .credential-auth-start-button {
    align-self: flex-start;
    margin-top: auto;
  }
}
</style>
