import type { SettingField, SettingsResponse } from '../types';

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
  let editVersion = 0;
  let currentFields: SettingField[] = [];
  let baseline: string | null = null;

  function snapshot(): string {
    return JSON.stringify(
      currentFields.map((field) => [
        field.key,
        field.type,
        field.type === 'tags' ? [...(tagValues[field.key] || [])] : form[field.key],
      ]),
    );
  }

  function isDirty(): boolean {
    return baseline !== null && snapshot() !== baseline;
  }

  function resetBaseline(): void {
    baseline = snapshot();
  }

  function updateBaseline(data: SettingsResponse): void {
    currentFields = [...data.fields];
    baseline = JSON.stringify(
      data.fields.map((field) => [
        field.key,
        field.type,
        field.type === 'tags'
          ? parseTags(data.settings[field.key], field.separator || ',')
          : (data.settings[field.key] ?? (field.nullable ? null : '')),
      ]),
    );
  }

  /**
   * 按 dirty 状态应用服务端设置；force 用于保存结果或显式刷新。
   */
  function applySettings(
    data: SettingsResponse | null | undefined,
    options: ApplySettingsOptions = {},
  ): boolean {
    if (!data) return false;
    if (isDirty() && !options.force) return false;
    fillFields(form, tagValues, data);
    currentFields = [...data.fields];
    resetBaseline();
    return true;
  }

  return {
    applySettings,
    markDirty: () => {
      editVersion += 1;
    },
    getEditVersion: () => editVersion,
    isDirty,
    resetBaseline,
    updateBaseline,
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
