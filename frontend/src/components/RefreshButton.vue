<script setup lang="ts">
import { computed, ref } from 'vue';
import { RefreshCw } from '@lucide/vue';
import CButton from './ui/CButton.vue';
import { useToast } from '../composables/useToast';

interface RefetchResult {
  isError?: boolean;
}

type ButtonSize = 'sm' | 'md' | 'lg';
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

/**
 * vue-query 的 `useQuery` 返回对象中，`isFetching` 被 Vue 包装为 `Ref<boolean>`、
 * `refetch` 仍是函数。这里用结构化类型而非 `QueryObserverResult` 的 Pick：
 * 后者 `isFetching` 是裸 `boolean`（core 类型），与 vue-query 的 `Ref<boolean>` 不匹配；
 * 且 `QueryObserverResult` 是泛型类型，作为 props 无法承载具体泛型参数。
 * 宽松结构化类型可兼容任意 `useQuery<TData, TError>` 返回值。
 */
const props = withDefaults(
  defineProps<{
    query: { isFetching: { value: boolean }; refetch: () => Promise<unknown> | unknown };
    label?: string;
    successMessage?: string;
    size?: ButtonSize;
    variant?: ButtonVariant;
  }>(),
  {
    successMessage: '已刷新',
    size: 'md',
    variant: 'secondary',
  },
);

const emit = defineEmits<{
  success: [result: unknown];
}>();

const toast = useToast();
const refreshing = ref(false);
const loading = computed(() => refreshing.value || props.query.isFetching.value);
const MINIMUM_LOADING_MS = 300;

function waitForMinimumLoading(): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, MINIMUM_LOADING_MS));
}

function isRefetchErrorResult(result: unknown): result is RefetchResult {
  return (
    typeof result === 'object' &&
    result !== null &&
    'isError' in result &&
    (result as RefetchResult).isError === true
  );
}

async function handleRefresh(): Promise<void> {
  if (loading.value) return;
  if (!window.navigator.onLine) {
    toast.warning('当前处于离线状态，请联网后重试');
    return;
  }

  refreshing.value = true;
  const minimumLoading = waitForMinimumLoading();
  try {
    const result = await props.query.refetch();
    if (isRefetchErrorResult(result)) return;

    emit('success', result);
    toast.success(props.successMessage);
  } finally {
    await minimumLoading;
    refreshing.value = false;
  }
}
</script>

<template>
  <CButton :loading="loading" :size="size" :variant="variant" @click="handleRefresh">
    <template #icon>
      <RefreshCw :size="16" />
    </template>
    {{ label ?? '刷新' }}
  </CButton>
</template>
