<script setup lang="ts">
import { ref } from 'vue';
import { X } from '@lucide/vue';
import CTag from './CTag.vue';

interface Props {
  modelValue?: string[];
  placeholder?: string;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: () => [],
  placeholder: '添加...',
});

const emit = defineEmits<{
  'update:modelValue': [value: string[]];
}>();

const inputValue = ref('');

function appendUniqueTags(candidates: string[]): void {
  const seen = new Set(props.modelValue);
  const additions = candidates.filter((tag) => {
    if (seen.has(tag)) return false;
    seen.add(tag);
    return true;
  });
  if (additions.length > 0) emit('update:modelValue', [...props.modelValue, ...additions]);
}

function onInput(event: Event): void {
  const input = event.target as HTMLInputElement;
  const value = input.value;
  if (!value.includes(',')) {
    inputValue.value = value;
    return;
  }

  const lastSeparatorIndex = value.lastIndexOf(',');
  const currentInput = value.slice(lastSeparatorIndex + 1);
  input.value = currentInput;
  inputValue.value = currentInput;
  const tags = value
    .slice(0, lastSeparatorIndex)
    .split(',')
    .map((segment) => segment.trim())
    .filter(Boolean);
  if (tags.length > 0) {
    appendUniqueTags(tags);
  }
}

function commit(): void {
  const trimmed = inputValue.value.trim();
  if (!trimmed) return;
  inputValue.value = '';
  appendUniqueTags([trimmed]);
}

function onKeyup(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.isComposing) {
    commit();
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Backspace' && inputValue.value === '') {
    const next = [...props.modelValue];
    next.pop();
    emit('update:modelValue', next);
  }
}

function removeAt(index: number): void {
  const next = [...props.modelValue];
  next.splice(index, 1);
  emit('update:modelValue', next);
}
</script>

<template>
  <div class="c-dynamic-tags flex min-w-0 flex-wrap items-center gap-1.5">
    <CTag v-for="(tag, index) in modelValue" :key="`${tag}-${index}`">
      <span class="min-w-0 truncate">{{ tag }}</span>
      <button
        type="button"
        class="c-dynamic-tags-remove -mr-0.5 inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full text-muted hover:text-text"
        :aria-label="`删除标签 ${tag}`"
        @click="removeAt(index)"
      >
        <X :size="12" />
      </button>
    </CTag>
    <input
      :value="inputValue"
      :placeholder="placeholder"
      class="h-[22px] w-[5rem] rounded-sm bg-transparent px-1 text-xs text-text outline-none placeholder:text-muted/60"
      @input="onInput"
      @keyup="onKeyup"
      @keydown="onKeydown"
      @blur="commit"
    />
  </div>
</template>
