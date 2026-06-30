<script setup lang="ts">
import { computed, type Component } from 'vue';
import { Inbox } from '@lucide/vue';

interface Props {
  icon?: Component;
  title?: string;
  description?: string;
}

const props = withDefaults(defineProps<Props>(), {
  icon: undefined,
  title: undefined,
  description: undefined,
});

const iconComponent = computed<Component>(() => props.icon ?? Inbox);
</script>

<template>
  <div class="flex flex-col items-center gap-2 py-12 text-center">
    <div v-if="$slots.icon" class="flex h-10 w-10 items-center justify-center text-muted/50">
      <slot name="icon" />
    </div>
    <div v-else class="c-empty-icon flex h-10 w-10 items-center justify-center text-muted/50">
      <component :is="iconComponent" :size="40" />
    </div>

    <div v-if="title" class="c-empty-title mt-1 text-[15px] font-semibold text-text">
      {{ title }}
    </div>

    <div v-if="description" class="c-empty-desc text-[13px] text-muted">
      {{ description }}
    </div>

    <div v-if="$slots.default" class="c-empty-action mt-3">
      <slot />
    </div>
  </div>
</template>
