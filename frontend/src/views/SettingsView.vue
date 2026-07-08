<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query';
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

const queryClient = useQueryClient();
const toast = useToast();
const form = reactive<Record<string, string | number | boolean | null>>({});
const tagValues = reactive<Record<string, string[]>>({});
const AUTO_ROTATION_KEY = 'CODEBUDDY_AUTO_ROTATION_ENABLED';
const ROTATION_COUNT_KEY = 'CODEBUDDY_ROTATION_COUNT';

const settingsQuery = useQuery({
  queryKey: ['admin-settings'],
  queryFn: adminApi.settings,
});

const fields = computed(() => settingsQuery.data.value?.fields || []);
const visibleFields = computed(() =>
  fields.value.filter(
    (field) => field.key !== ROTATION_COUNT_KEY || form[AUTO_ROTATION_KEY] === true,
  ),
);

const settingsForm = createSettingsFormController(form, tagValues);
let submittedEditVersion = settingsForm.getEditVersion();
watch(
  () => settingsQuery.data.value,
  (data) => {
    settingsForm.applySettings(data);
    if (data && ROTATION_COUNT_KEY in form) {
      form[ROTATION_COUNT_KEY] = normalizeRotationCount(form[ROTATION_COUNT_KEY]);
    }
  },
  { immediate: true },
);

const saveMutation = useMutation({
  mutationFn: () => {
    submittedEditVersion = settingsForm.getEditVersion();
    return adminApi.saveSettings(buildPayload());
  },
  onSuccess: async (data: SettingsResponse) => {
    if (settingsForm.getEditVersion() === submittedEditVersion) {
      applyServerSettings(data, true);
    }
    toast.success('设置已保存');
    await queryClient.invalidateQueries({ queryKey: ['admin-settings'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-status'] });
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
  settingsForm.applySettings(data, { force });
  if (ROTATION_COUNT_KEY in form) {
    form[ROTATION_COUNT_KEY] = normalizeRotationCount(form[ROTATION_COUNT_KEY]);
  }
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
      payload[field.key] = normalizeRotationCount(form[field.key]);
      continue;
    }
    payload[field.key] = form[field.key];
  }
  return payload;
}

function selectOptions(field: SettingField) {
  return (field.options || []).map((item) => ({ label: item, value: item }));
}

function normalizeRotationCount(value: string | number | boolean | null): number {
  if (typeof value === 'number') {
    return Number.isInteger(value) && value >= 1 ? value : 1;
  }
  if (typeof value === 'string') {
    const parsed = Number(value.trim());
    return Number.isInteger(parsed) && parsed >= 1 ? parsed : 1;
  }
  return 1;
}

function updateBooleanField(field: SettingField, value: boolean) {
  settingsForm.markDirty();
  form[field.key] = value;
  if (field.key === AUTO_ROTATION_KEY && value) {
    form[ROTATION_COUNT_KEY] = normalizeRotationCount(form[ROTATION_COUNT_KEY]);
  }
}

function updateNumberField(field: SettingField, value: number | null) {
  settingsForm.markDirty();
  if (field.key === ROTATION_COUNT_KEY) {
    form[field.key] = normalizeRotationCount(value);
    return;
  }
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
</script>

<template>
  <CCard title="服务配置">
    <template #header-extra>
      <div class="toolbar-actions flex items-center gap-2">
        <RefreshButton :query="settingsQuery" @success="handleRefreshSuccess" />
        <CButton
          variant="primary"
          :loading="saveMutation.isPending.value"
          :class="{ 'animate-success': savedFlash }"
          @click="saveMutation.mutate()"
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
            <span class="inline-flex w-full min-w-0 max-w-full items-start justify-start gap-1.5 md:justify-end">
              <span class="min-w-0 max-w-full whitespace-normal break-words">{{ field.label }}</span>
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
          <CInputNumber
            v-else-if="field.type === 'number'"
            class="settings-number-input md:max-w-64"
            :model-value="form[field.key] as number | null"
            :min="field.min"
            :max="field.max"
            :step="field.step || 1"
            :clearable="field.key !== ROTATION_COUNT_KEY"
            @update:model-value="updateNumberField(field, $event)"
          />
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
