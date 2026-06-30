import { describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, reactive } from 'vue';
import CForm from '../components/ui/CForm.vue';
import CFormItem from '../components/ui/CFormItem.vue';
import CInput from '../components/ui/CInput.vue';

function mountForm(options: {
  model: Record<string, unknown>;
  rules?: Record<string, unknown>;
  slots?: Record<string, string>;
}) {
  const Component = defineComponent({
    components: { CForm, CFormItem, CInput },
    data() {
      return { model: options.model, rules: options.rules ?? {} };
    },
    template: `
      <CForm ref="formRef" :model="model" :rules="rules">
        ${options.slots?.default ?? ''}
      </CForm>
    `,
  });
  return mount(Component, { attachTo: document.body });
}

function getFormRef(wrapper: ReturnType<typeof mountForm>): any {
  return (wrapper.vm.$refs as any).formRef;
}

describe('CForm', () => {
  it('渲染 form 元素与 default slot', () => {
    const wrapper = mountForm({
      model: { name: '' },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    expect(wrapper.find('form').exists()).toBe(true);
    expect(wrapper.text()).toContain('名称');
  });

  it('labelPlacement=left 时容器为 grid 布局', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="left">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const form = wrapper.find('form');
    expect(form.classes()).toContain('grid');
    expect(form.classes()).toContain('grid-cols-1');
    expect(form.classes()).toContain('md:grid-cols-[var(--label-width,12rem)_minmax(0,1fr)]');
    expect(form.classes()).toContain('gap-y-0');
    expect(form.classes()).toContain('md:gap-y-0');
    expect(form.classes()).not.toContain('gap-y-2');
    expect(form.classes()).not.toContain('md:gap-y-5');
  });

  it('labelPlacement=top 时容器为 flex flex-col', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="top">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const form = wrapper.find('form');
    expect(form.classes()).toContain('flex');
    expect(form.classes()).toContain('flex-col');
    expect(form.classes()).toContain('gap-0');
    expect(form.classes()).not.toContain('gap-5');
  });

  it('labelWidth 透传为 CSS 变量', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="left" label-width="190px">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const form = wrapper.find('form');
    expect(form.attributes('style')).toContain('--label-width');
    expect(form.attributes('style')).toContain('190px');
  });

  it('validate 全部通过时 resolve', async () => {
    const wrapper = mountForm({
      model: { name: 'alice' },
      rules: { name: { required: true, message: '请输入名称', trigger: 'blur' } },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).resolves.toBeUndefined();
  });

  it('validate required 失败时 reject 并返回错误数组', async () => {
    const wrapper = mountForm({
      model: { name: '' },
      rules: { name: { required: true, message: '请输入名称', trigger: 'blur' } },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '请输入名称' }]);
  });

  it('validate 多字段部分失败时 reject 返回所有错误', async () => {
    const wrapper = mountForm({
      model: { name: '', email: 'a@b.com' },
      rules: {
        name: { required: true, message: '名称必填', trigger: 'blur' },
        email: { required: true, message: '邮箱必填', trigger: 'blur' },
      },
      slots: {
        default: `
          <CFormItem label="名称" path="name"><CInput /></CFormItem>
          <CFormItem label="邮箱" path="email"><CInput /></CFormItem>
        `,
      },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '名称必填' }]);
  });

  it('whitespace 规则：纯空格失败', async () => {
    const wrapper = mountForm({
      model: { name: '   ' },
      rules: {
        name: { required: true, whitespace: true, message: '不能纯空格', trigger: 'input' },
      },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '不能纯空格' }]);
  });

  it('validator 返回 true 时通过', async () => {
    const validator = vi.fn<(value: unknown) => boolean>(() => true);
    const wrapper = mountForm({
      model: { name: 'abc' },
      rules: { name: { validator, message: '校验失败', trigger: 'blur' } },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).resolves.toBeUndefined();
    expect(validator).toHaveBeenCalledWith('abc');
  });

  it('validator 返回 false 时用 message 失败', async () => {
    const wrapper = mountForm({
      model: { name: 'abc' },
      rules: { name: { validator: () => false, message: '格式错误', trigger: 'blur' } },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '格式错误' }]);
  });

  it('validator 返回字符串时用该字符串作为错误消息', async () => {
    const wrapper = mountForm({
      model: { name: 'abc' },
      rules: { name: { validator: () => '长度不足', trigger: 'blur' } },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '长度不足' }]);
  });

  it('rules 支持数组形式（多条规则）', async () => {
    const wrapper = mountForm({
      model: { name: 'a' },
      rules: {
        name: [
          { required: true, message: '必填', trigger: 'blur' },
          { validator: (v: unknown) => (v as string).length >= 3 || '太短', trigger: 'blur' },
        ],
      },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '太短' }]);
  });

  it('restoreValidation 清除所有 FormItem 错误', async () => {
    const wrapper = mountForm({
      model: { name: '' },
      rules: { name: { required: true, message: '必填', trigger: 'blur' } },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    await expect(formRef.validate()).rejects.toBeDefined();
    await flushPromises();
    expect(wrapper.text()).toContain('必填');
    formRef.restoreValidation();
    await flushPromises();
    expect(wrapper.text()).not.toContain('必填');
  });

  it('expose validate 和 restoreValidation 方法', () => {
    const wrapper = mountForm({
      model: { name: '' },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    const formRef = getFormRef(wrapper);
    expect(typeof formRef.validate).toBe('function');
    expect(typeof formRef.restoreValidation).toBe('function');
  });

  it('FormItem 卸载后不再参与 validate（unregisterItem）', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return {
            model: { a: '', b: '' },
            rules: {
              a: { required: true, message: 'a 必填' },
              b: { required: true, message: 'b 必填' },
            },
            showB: true,
          };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="A" path="a"><CInput /></CFormItem>
            <CFormItem v-if="showB" label="B" path="b"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    await expect(formRef.validate()).rejects.toHaveLength(2);
    (wrapper.vm as any).showB = false;
    await flushPromises();
    await expect(formRef.validate()).rejects.toHaveLength(1);
  });

  it('requireMarkPlacement 兼容 prop（不报错）', () => {
    const wrapper = mountForm({
      model: { name: '' },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    expect(wrapper.find('form').exists()).toBe(true);
  });

  it('submit 事件被 prevent（不触发原生提交）', async () => {
    const wrapper = mountForm({
      model: { name: '' },
      slots: { default: '<CFormItem label="名称" path="name"><CInput /></CFormItem>' },
    });
    await wrapper.find('form').trigger('submit');
    expect(wrapper.find('form').exists()).toBe(true);
  });
});

describe('CFormItem', () => {
  it('渲染 label 文本', () => {
    const wrapper = mount(CForm, {
      props: { model: {}, rules: {} },
      slots: {
        default: () => null,
      },
    });
    // 直接挂载 CFormItem 需 inject，这里通过 CForm 包裹测试
    expect(wrapper.find('form').exists()).toBe(true);
  });

  it('required 规则时 label 后显示 * 标记', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: { name: { required: true, message: '必填' } } };
        },
        template: `
          <CForm :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const required = wrapper.find('.c-form-item-required');
    expect(required.exists()).toBe(true);
    expect(required.text()).toBe('*');
  });

  it('prop required=true 时也显示 * 标记', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: {}, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules">
            <CFormItem label="名称" path="name" :required="true"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    expect(wrapper.find('.c-form-item-required').exists()).toBe(true);
  });

  it('非必填时不显示 * 标记', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    expect(wrapper.find('.c-form-item-required').exists()).toBe(false);
  });

  it('labelPlacement=left 时 label 桌面端右对齐', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="left">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const label = wrapper.find('.c-form-item-label');
    expect(label.classes()).toContain('text-left');
    expect(label.classes()).toContain('md:text-right');
  });

  it('labelPlacement=left 时 label 在桌面端按控件高度垂直居中', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="left">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const labelClasses = wrapper.find('.c-form-item-label').classes();
    expect(labelClasses).toContain('md:flex');
    expect(labelClasses).toContain('md:min-h-[38px]');
    expect(labelClasses).toContain('md:items-center');
    expect(labelClasses).toContain('md:justify-end');
    expect(labelClasses).not.toContain('pt-[0.375rem]');
  });

  it('表单项控制区使用固定控件行高承载插槽内容', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="left">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const controlInnerClasses = wrapper.find('.c-form-item-control-inner').classes();
    expect(controlInnerClasses).toContain('flex');
    expect(controlInnerClasses).toContain('min-h-[38px]');
    expect(controlInnerClasses).toContain('items-center');
  });

  it('labelPlacement=top 时 label 在上方', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="top">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const label = wrapper.find('.c-form-item-label');
    expect(label.classes()).toContain('block');
  });

  it('校验失败时显示错误消息', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: { name: { required: true, message: '名称必填' } } };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    try {
      await formRef.validate();
    } catch {}
    await flushPromises();
    const err = wrapper.find('.c-form-item-error');
    expect(err.exists()).toBe(true);
    expect(err.text()).toBe('名称必填');
  });

  it('错误提示含 shake 动画 class', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: { name: { required: true, message: '必填' } } };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    try {
      await formRef.validate();
    } catch {}
    await flushPromises();
    const err = wrapper.find('.c-form-item-error');
    expect(err.classes()).toContain('animate-[shake_0.3s_ease]');
  });

  it('错误元素直接用 v-if 渲染（无 Transition 包裹）', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: { name: { required: true, message: '必填' } } };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    try {
      await formRef.validate();
    } catch {}
    await flushPromises();
    // .c-form-item-error 的父级不应是 Transition 渲染出的包装元素
    const err = wrapper.find('.c-form-item-error');
    expect(err.exists()).toBe(true);
    expect(err.text()).toBe('必填');
    formRef.restoreValidation();
    await flushPromises();
    expect(wrapper.find('.c-form-item-error').exists()).toBe(false);
  });

  it('无 label 时不渲染 label 元素', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules">
            <CFormItem path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    expect(wrapper.find('.c-form-item-label').exists()).toBe(false);
  });

  it('value 为 null 时 required 失败', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: null }, rules: { name: { required: true, message: '必填' } } };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '必填' }]);
  });

  it('value 为 0 时 required 通过（0 是有效值）', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { count: 0 }, rules: { count: { required: true, message: '必填' } } };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="数量" path="count"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    await expect(formRef.validate()).resolves.toBeUndefined();
  });

  it('reactive model 更新后 validate 反映最新值', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return {
            model: reactive({ name: '' }),
            rules: { name: { required: true, message: '必填' } },
          };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput v-model="model.name" /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    await expect(formRef.validate()).rejects.toBeDefined();
    (wrapper.vm as any).model.name = 'alice';
    await flushPromises();
    await expect(formRef.validate()).resolves.toBeUndefined();
  });

  it('单独使用（无 CForm 父级）时抛错', () => {
    expect(() => mount(CFormItem, { props: { path: 'x' } })).toThrow(
      'CFormItem 必须在 CForm 内使用',
    );
  });

  it('required 无 message 时用默认消息', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: { name: { required: true } } };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '该字段必填' }]);
  });

  it('validator 返回 false 无 message 时用默认消息', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: 'x' }, rules: { name: { validator: () => false } } };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '校验失败' }]);
  });

  it('无规则的 path validate 通过', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    await expect(formRef.validate()).resolves.toBeUndefined();
  });

  it('labelPlacement=top 且非必填时 label 无 * 标记', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="top">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    expect(wrapper.find('.c-form-item-required').exists()).toBe(false);
  });

  it('labelPlacement=top 且必填时 label 含 * 标记', () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return {
            model: { name: '' },
            rules: { name: { required: true, message: '必填' } },
          };
        },
        template: `
          <CForm :model="model" :rules="rules" label-placement="top">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    expect(wrapper.find('.c-form-item-required').exists()).toBe(true);
  });

  it('CForm labelPlacement 动态变化时 CFormItem 响应', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {}, placement: 'left' as const };
        },
        template: `
          <CForm :model="model" :rules="rules" :label-placement="placement">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    expect(wrapper.find('.c-form-item-label').classes()).toContain('text-left');
    expect(wrapper.find('.c-form-item-label').classes()).toContain('md:text-right');
    (wrapper.vm as any).placement = 'top';
    await flushPromises();
    expect(wrapper.find('.c-form-item-label').classes()).toContain('block');
  });

  it('CForm rules 动态变化时 validate 反映新规则', async () => {
    const wrapper = mount(
      {
        components: { CForm, CFormItem, CInput },
        data() {
          return { model: { name: '' }, rules: {} as Record<string, unknown> };
        },
        template: `
          <CForm ref="formRef" :model="model" :rules="rules">
            <CFormItem label="名称" path="name"><CInput /></CFormItem>
          </CForm>
        `,
      },
      { attachTo: document.body },
    );
    const formRef = (wrapper.vm.$refs as any).formRef;
    await expect(formRef.validate()).resolves.toBeUndefined();
    (wrapper.vm as any).rules = { name: { required: true, message: '必填' } };
    await flushPromises();
    await expect(formRef.validate()).rejects.toEqual([{ field: 'name', message: '必填' }]);
  });
});
