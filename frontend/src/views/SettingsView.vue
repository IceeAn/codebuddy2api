<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
import { onBeforeRouteLeave } from 'vue-router';
import { CircleHelp, Save } from '@lucide/vue';
import { adminApi } from '../api/admin';
import type { SettingField, SettingsResponse } from '../types';
import { createSettingsFormController } from '../utils/settingsForm';
import { useToast } from '../composables/useToast';
import CCard from '../components/ui/CCard.vue';
import CAlert from '../components/ui/CAlert.vue';
import CSpin from '../components/ui/CSpin.vue';
import CForm from '../components/ui/CForm.vue';
import CFormItem from '../components/ui/CFormItem.vue';
import CSelect from '../components/ui/CSelect.vue';
import CSwitch from '../components/ui/CSwitch.vue';
import CInputNumber from '../components/ui/CInputNumber.vue';
import CDynamicTags from '../components/ui/CDynamicTags.vue';
import CInput from '../components/ui/CInput.vue';
import CButton from '../components/ui/CButton.vue';
import CTooltip from '../components/ui/CTooltip.vue';
import RefreshButton from '../components/RefreshButton.vue';
import { useSessionStore } from '../stores/session';
import { adminQueryKeys } from '../utils/adminQueryKeys';

const queryClient = useQueryClient();
const session = useSessionStore();
const queryKeys = adminQueryKeys(session.username);
const toast = useToast();
const form = reactive<Record<string, string | number | boolean | null>>({});
const tagValues = reactive<Record<string, string[]>>({});
const AUTO_ROTATION_KEY = 'CODEBUDDY_AUTO_ROTATION_ENABLED';
const ROTATION_COUNT_KEY = 'CODEBUDDY_ROTATION_COUNT';

const settingsQuery = useQuery({
  queryKey: queryKeys.settings,
  queryFn: adminApi.settings,
});

const fields = computed(() => settingsQuery.data.value?.fields || []);
const visibleFields = computed(() =>
  fields.value.filter(
    (field) => field.key !== ROTATION_COUNT_KEY || form[AUTO_ROTATION_KEY] === true,
  ),
);

const settingsForm = createSettingsFormController(form, tagValues);
const baselineRevision = ref(0);
const isDirty = computed(
  () => ({ revision: baselineRevision.value, dirty: settingsForm.isDirty() }).dirty,
);
let submittedEditVersion = settingsForm.getEditVersion();
watch(
  () => settingsQuery.data.value,
  (data) => {
    if (data) applyServerSettings(data, false);
  },
  { immediate: true },
);

function parseRotationCount(value: string | number | boolean | null): number | null {
  if (typeof value === 'number') {
    return Number.isInteger(value) && value >= 1 ? value : null;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value.trim());
    return Number.isInteger(parsed) && parsed >= 1 ? parsed : null;
  }
  return null;
}

function validateNumberField(field: SettingField): string | null {
  const value = form[field.key];
  if (field.key === ROTATION_COUNT_KEY) {
    return parseRotationCount(value) === null ? '轮换次数必须是大于或等于 1 的整数' : null;
  }
  if (value === null || value === '') {
    return field.nullable ? null : `${field.label}不能为空`;
  }
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return `${field.label}必须是有效数字`;
  }
  if (field.min !== undefined && value < field.min) {
    return `${field.label}不能小于 ${field.min}`;
  }
  if (field.max !== undefined && value > field.max) {
    return `${field.label}不能大于 ${field.max}`;
  }
  return null;
}

const numberFieldErrors = computed<Record<string, string>>(() => {
  const errors: Record<string, string> = {};
  for (const field of fields.value) {
    if (field.type !== 'number') continue;
    const error = validateNumberField(field);
    if (error) errors[field.key] = error;
  }
  return errors;
});
const rotationCountError = computed(() => numberFieldErrors.value[ROTATION_COUNT_KEY] ?? null);
const formError = computed(() => Object.values(numberFieldErrors.value)[0] ?? null);
const formInvalid = computed(() => formError.value !== null);
const settingsLoaded = computed(() => Boolean(settingsQuery.data.value) && fields.value.length > 0);

const saveMutation = useMutation({
  mutationFn: () => {
    if (formError.value) throw new Error(formError.value);
    submittedEditVersion = settingsForm.getEditVersion();
    return adminApi.saveSettings(buildPayload());
  },
  onSuccess: async (data: SettingsResponse) => {
    if (settingsForm.getEditVersion() === submittedEditVersion) {
      applyServerSettings(data, true);
    } else {
      settingsForm.updateBaseline(data);
      baselineRevision.value += 1;
    }
    toast.success('设置已保存');
    await queryClient.invalidateQueries({ queryKey: queryKeys.settings });
    await queryClient.invalidateQueries({ queryKey: queryKeys.status });
  },
});

/**
 * 显式刷新代表用户希望以服务端真实值为准，成功后覆盖本地未保存编辑。
 */
function handleRefreshSuccess(result: unknown): void {
  const data = extractRefetchData(result);
  if (data) {
    applyServerSettings(data, true);
  }
}

async function refetchSettings(): Promise<unknown> {
  if (isDirty.value && !window.confirm('当前有未保存的设置，确定放弃修改并刷新吗？')) {
    return { isError: true, cancelled: true };
  }
  return settingsQuery.refetch();
}

const settingsRefreshQuery = {
  isFetching: settingsQuery.isFetching,
  refetch: refetchSettings,
};

/**
 * 从 refetch 结果中提取服务端设置；无数据时保持当前表单。
 */
function extractRefetchData(result: unknown): SettingsResponse | undefined {
  if (typeof result !== 'object' || result === null || !('data' in result)) return undefined;
  return result.data as SettingsResponse | undefined;
}

/**
 * 应用服务端设置并维护需要在前端展示为正整数的字段。
 */
function applyServerSettings(data: SettingsResponse, force: boolean): void {
  const applied = settingsForm.applySettings(data, { force });
  if (!applied) return;
  const rotationCount = form[ROTATION_COUNT_KEY];
  const parsedRotationCount = parseRotationCount(rotationCount);
  if (typeof rotationCount === 'string' && parsedRotationCount !== null) {
    form[ROTATION_COUNT_KEY] = parsedRotationCount;
    settingsForm.resetBaseline();
  }
  baselineRevision.value += 1;
}

const savedFlash = ref(false);
watch(
  () => saveMutation.isSuccess.value,
  (success) => {
    if (!success) return;
    savedFlash.value = true;
    window.setTimeout(() => {
      savedFlash.value = false;
    }, 600);
  },
);

function buildPayload() {
  const payload: Record<string, unknown> = {};
  for (const field of fields.value) {
    if (field.type === 'tags') {
      payload[field.key] = (tagValues[field.key] || []).join(field.separator || ',');
      continue;
    }
    if (field.nullable && (form[field.key] === null || form[field.key] === '')) {
      payload[field.key] = '';
      continue;
    }
    if (field.key === ROTATION_COUNT_KEY) {
      payload[field.key] = parseRotationCount(form[field.key]) ?? form[field.key];
      continue;
    }
    payload[field.key] = form[field.key];
  }
  return payload;
}

function selectOptions(field: SettingField) {
  return (field.options || []).map((item) => ({ label: item, value: item }));
}

function updateBooleanField(field: SettingField, value: boolean) {
  settingsForm.markDirty();
  form[field.key] = value;
}

function updateNumberField(field: SettingField, value: number | null) {
  settingsForm.markDirty();
  form[field.key] = value;
}

/**
 * 记录用户编辑并写入普通表单字段。
 */
function updateField(field: SettingField, value: string | number | boolean | null) {
  settingsForm.markDirty();
  form[field.key] = value;
}

/**
 * 记录用户编辑并写入标签字段。
 */
function updateTags(field: SettingField, value: string[]) {
  settingsForm.markDirty();
  tagValues[field.key] = value;
}

function saveSettings(): void {
  if (formError.value) {
    toast.error(formError.value);
    return;
  }
  if (!settingsLoaded.value || !isDirty.value || saveMutation.isPending.value) return;
  saveMutation.mutate();
}

function confirmLeave(): boolean {
  return !isDirty.value || window.confirm('当前有未保存的设置，确定放弃修改并离开吗？');
}

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  if (!isDirty.value) return;
  event.preventDefault();
  event.returnValue = '';
}

onBeforeRouteLeave(confirmLeave);
onMounted(() => window.addEventListener('beforeunload', handleBeforeUnload));
onBeforeUnmount(() => window.removeEventListener('beforeunload', handleBeforeUnload));
</script>

<template>
  <CCard title="服务配置">
    <template #header-extra>
      <div class="toolbar-actions flex items-center gap-2">
        <RefreshButton :query="settingsRefreshQuery" @success="handleRefreshSuccess" />
        <CButton
          variant="primary"
          :loading="saveMutation.isPending.value"
          :disabled="!settingsLoaded || !isDirty || formInvalid || saveMutation.isPending.value"
          :class="{ 'animate-success': savedFlash }"
          @click="saveSettings"
        >
          <template #icon>
            <Save :size="16" />
          </template>
          保存
        </CButton>
      </div>
    </template>

    <CAlert v-if="settingsQuery.isError.value" type="error" :show-icon="true">
      <div class="toolbar flex items-center gap-2">
        <span>加载配置失败</span>
        <RefreshButton :query="settingsQuery" label="重试" size="sm" />
      </div>
    </CAlert>

    <CAlert
      v-else-if="!settingsQuery.isLoading.value && fields.length === 0"
      type="info"
      :show-icon="false"
    >
      暂无可配置项
    </CAlert>

    <div
      v-else-if="settingsQuery.isLoading.value"
      class="settings-loading grid min-h-24 place-items-center"
    >
      <CSpin size="lg" />
    </div>

    <div v-else>
      <CForm :model="form" label-placement="left" label-width="fit-content(14rem)">
        <CFormItem
          v-for="field in visibleFields"
          :key="field.key"
          :label="field.label"
          :path="field.key"
        >
          <template v-if="field.description" #label>
            <span
              class="inline-flex w-full max-w-full min-w-0 items-start justify-start gap-1.5 md:justify-end"
            >
              <span class="max-w-full min-w-0 break-words whitespace-normal">{{
                field.label
              }}</span>
              <CTooltip :content="field.description" placement="top">
                <span
                  class="setting-help-trigger inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-muted transition-colors duration-(--duration-fast) hover:text-text"
                  :aria-label="`${field.label}说明`"
                  role="img"
                >
                  <CircleHelp :size="15" />
                </span>
              </CTooltip>
            </span>
          </template>
          <CSelect
            v-if="field.type === 'select'"
            :model-value="form[field.key] as string | number"
            :options="selectOptions(field)"
            @update:model-value="updateField(field, $event)"
          />
          <CSwitch
            v-else-if="field.type === 'boolean'"
            :model-value="form[field.key] as boolean"
            @update:model-value="updateBooleanField(field, $event)"
          />
          <div v-else-if="field.type === 'number'" class="w-full">
            <CInputNumber
              class="settings-number-input md:max-w-64"
              :model-value="form[field.key] as number | null"
              :min="field.min"
              :max="field.max"
              :step="field.step || 1"
              :clearable="field.key !== ROTATION_COUNT_KEY"
              :aria-invalid="Boolean(numberFieldErrors[field.key])"
              @update:model-value="updateNumberField(field, $event)"
            />
            <p
              v-if="numberFieldErrors[field.key]"
              class="mt-1 text-xs text-tone-error"
              role="alert"
            >
              {{ numberFieldErrors[field.key] }}
            </p>
          </div>
          <CDynamicTags
            v-else-if="field.type === 'tags'"
            :model-value="tagValues[field.key]"
            @update:model-value="updateTags(field, $event)"
          />
          <CInput
            v-else
            :model-value="(form[field.key] as string | null) ?? ''"
            @update:model-value="updateField(field, $event)"
          />
        </CFormItem>
      </CForm>
    </div>
  </CCard>
</template>
