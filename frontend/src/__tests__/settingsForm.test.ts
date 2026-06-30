import { describe, expect, it } from 'vitest';

import * as settingsForm from '../utils/settingsForm';
import { createSettingsFormController } from '../utils/settingsForm';
import type { SettingsResponse, SettingField } from '../types';

type FormMap = Record<string, string | number | boolean | null>;

function makeData(
  settings: FormMap,
  fields: Array<Partial<SettingField> & { key: string; type: SettingField['type'] }>,
): SettingsResponse {
  return { settings, fields: fields as SettingField[] };
}

describe('createSettingsFormController', () => {
  it('不导出旧的单次初始化器', () => {
    const legacyExport = ['createSettingsForm', 'Initializer'].join('');

    expect(settingsForm).not.toHaveProperty(legacyExport);
  });

  it('首次 data 到达时填充 form 与 tagValues', () => {
    const form: FormMap = {};
    const tagValues: Record<string, string[]> = {};
    const controller = createSettingsFormController(form, tagValues);

    controller.applySettings(
      makeData({ model: 'glm-5.2', temperature: 1, enabled: true, tags: 'a,b,c' }, [
        { key: 'model', label: '模型', type: 'text' },
        { key: 'temperature', label: '温度', type: 'number' },
        { key: 'enabled', label: '启用', type: 'boolean' },
        { key: 'tags', label: '标签', type: 'tags', separator: ',' },
      ]),
    );

    expect(form.model).toBe('glm-5.2');
    expect(form.temperature).toBe(1);
    expect(form.enabled).toBe(true);
    expect(tagValues.tags).toEqual(['a', 'b', 'c']);
  });

  it('表单未 dirty 时后续服务端数据会同步到表单', () => {
    const form: FormMap = {};
    const controller = createSettingsFormController(form, {});

    controller.applySettings(
      makeData({ model: 'glm-5.1' }, [{ key: 'model', label: '模型', type: 'text' }]),
    );
    controller.applySettings(
      makeData({ model: 'deepseek-v4-pro' }, [{ key: 'model', label: '模型', type: 'text' }]),
    );

    expect(form.model).toBe('deepseek-v4-pro');
  });

  it('表单 dirty 时自动刷新不覆盖用户编辑', () => {
    const form: FormMap = {};
    const controller = createSettingsFormController(form, {});

    controller.applySettings(
      makeData({ model: 'glm-5.1' }, [{ key: 'model', label: '模型', type: 'text' }]),
    );
    form.model = 'user-edit';
    controller.markDirty();
    controller.applySettings(
      makeData({ model: 'deepseek-v4-pro' }, [{ key: 'model', label: '模型', type: 'text' }]),
    );

    expect(form.model).toBe('user-edit');
  });

  it('force apply 会覆盖 dirty 表单并重置 dirty 状态', () => {
    const form: FormMap = {};
    const tagValues: Record<string, string[]> = {};
    const controller = createSettingsFormController(form, tagValues);

    controller.applySettings(
      makeData({ tags: 'a,b' }, [{ key: 'tags', label: '标签', type: 'tags' }]),
    );
    tagValues.tags = ['user-edit'];
    controller.markDirty();
    controller.applySettings(
      makeData({ tags: 'server' }, [{ key: 'tags', label: '标签', type: 'tags' }]),
      { force: true },
    );

    expect(tagValues.tags).toEqual(['server']);
    controller.applySettings(
      makeData({ tags: 'next' }, [{ key: 'tags', label: '标签', type: 'tags' }]),
    );
    expect(tagValues.tags).toEqual(['next']);
  });

  it('每次用户编辑都会递增编辑版本', () => {
    const controller = createSettingsFormController({}, {});

    expect(controller.getEditVersion()).toBe(0);
    controller.markDirty();
    expect(controller.getEditVersion()).toBe(1);
    controller.markDirty();
    expect(controller.getEditVersion()).toBe(2);
  });

  it('data 为 null/undefined 时不填充', () => {
    const form: FormMap = {};
    const tagValues: Record<string, string[]> = {};
    const controller = createSettingsFormController(form, tagValues);

    controller.applySettings(null);
    controller.applySettings(undefined);

    expect(Object.keys(form)).toHaveLength(0);
    expect(Object.keys(tagValues)).toHaveLength(0);
  });

  it('nullable 字段值为 null 时保留 null，缺失值使用空字符串', () => {
    const form: FormMap = {};
    const controller = createSettingsFormController(form, {});

    controller.applySettings(
      makeData({ a: null, c: 'x' }, [
        { key: 'a', label: 'A', type: 'text', nullable: true },
        { key: 'b', label: 'B', type: 'text' },
        { key: 'c', label: 'C', type: 'text' },
      ]),
    );

    expect(form.a).toBeNull();
    expect(form.b).toBe('');
    expect(form.c).toBe('x');
  });

  it('tags 字段使用自定义 separator 拆分并 trim/过滤空项', () => {
    const tagValues: Record<string, string[]> = {};
    const controller = createSettingsFormController({}, tagValues);

    controller.applySettings(
      makeData({ list: 'a ; b ; ; c ' }, [
        { key: 'list', label: '列表', type: 'tags', separator: ';' },
      ]),
    );

    expect(tagValues.list).toEqual(['a', 'b', 'c']);
  });

  it('tags 字段值为空时返回空数组', () => {
    const tagValues: Record<string, string[]> = {};
    const controller = createSettingsFormController({}, tagValues);

    controller.applySettings(
      makeData({ list: '' }, [{ key: 'list', label: '列表', type: 'tags', separator: ',' }]),
    );

    expect(tagValues.list).toEqual([]);
  });

  it('tags 字段未提供 separator 时使用逗号', () => {
    const tagValues: Record<string, string[]> = {};
    const controller = createSettingsFormController({}, tagValues);

    controller.applySettings(
      makeData({ list: 'a,b' }, [{ key: 'list', label: '列表', type: 'tags' }]),
    );

    expect(tagValues.list).toEqual(['a', 'b']);
  });
});
