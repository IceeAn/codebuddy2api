import { defineComponent, h, type PropType } from 'vue';

interface QueryStub {
  isFetching: { value: boolean };
  refetch: () => Promise<unknown> | unknown;
}

function isErrorResult(result: unknown): boolean {
  return (
    typeof result === 'object' && result !== null && 'isError' in result && result.isError === true
  );
}

/** 视图测试专用：保留 RefreshButton 的查询与 success 事件契约，不引入 300ms 动画计时。 */
export const RefreshButtonStub = defineComponent({
  name: 'RefreshButton',
  props: {
    query: { type: Object as PropType<QueryStub>, required: true },
    label: String,
    size: String,
    variant: String,
  },
  emits: ['success'],
  setup(props, { emit }) {
    async function refresh(): Promise<void> {
      if (props.query.isFetching.value) return;
      const result = await props.query.refetch();
      if (!isErrorResult(result)) emit('success', result);
    }

    return () =>
      h(
        'button',
        {
          class: 'refresh-button-stub',
          disabled: props.query.isFetching.value,
          'data-size': props.size ?? '',
          'data-variant': props.variant ?? '',
          onClick: refresh,
        },
        props.label ?? '刷新',
      );
  },
});
