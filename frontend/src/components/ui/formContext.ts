import type { ComputedRef, InjectionKey } from 'vue';

export interface FormItemControlContext {
  controlId: string;
  labelId: ComputedRef<string | undefined>;
  errorId: string;
  invalid: ComputedRef<boolean>;
  describedBy: ComputedRef<string | undefined>;
  onInput: () => void;
  onBlur: () => void;
}

export const formItemControlKey: InjectionKey<FormItemControlContext> =
  Symbol('c-form-item-control');
