import type { SettingsResponse } from '../types';

interface ApplySettingsOptions {
  force?: boolean;
}

/**
 * 管理设置表单与服务端数据之间的同步边界。
 * 表单保持干净时接收服务端最新值；用户编辑后只允许显式刷新或保存结果覆盖。
 */
export function createSettingsFormController(
  form: Record<string, string | number | boolean | null>,
  tagValues: Record<string, string[]>,
) {
  let dirty = false;
  let editVersion = 0;

  /**
   * 按 dirty 状态应用服务端设置；force 用于保存结果或显式刷新。
   */
  function applySettings(
    data: SettingsResponse | null | undefined,
    options: ApplySettingsOptions = {},
  ): void {
    if (!data) return;
    if (dirty && !options.force) return;
    fillFields(form, tagValues, data);
    if (options.force) dirty = false;
  }

  return {
    applySettings,
    markDirty: () => {
      dirty = true;
      editVersion += 1;
    },
    getEditVersion: () => editVersion,
  };
}

function fillFields(
  form: Record<string, string | number | boolean | null>,
  tagValues: Record<string, string[]>,
  data: SettingsResponse,
): void {
  for (const field of data.fields) {
    const value = data.settings[field.key];
    if (field.type === 'tags') {
      tagValues[field.key] = parseTags(value, field.separator || ',');
    } else {
      form[field.key] = value ?? (field.nullable ? null : '');
    }
  }
}

function parseTags(
  value: string | number | boolean | null | undefined,
  separator: string,
): string[] {
  return String(value || '')
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean);
}
