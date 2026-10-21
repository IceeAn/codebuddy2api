<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { BookOpen, ChevronDown } from '@lucide/vue';
import documents from 'virtual:usage-docs';
import CAlert from '../components/ui/CAlert.vue';
import CButton from '../components/ui/CButton.vue';
import { chunkLoadRecovery } from '../utils/chunkLoadRecovery';

const route = useRoute();
const article = ref<HTMLElement | null>(null);
const navigationOpen = ref(false);
const currentDocument = computed(() => {
  const id = route.query.doc === undefined ? 'README.md' : route.query.doc;
  return documents.find((document) => document.id === id);
});
const anchor = computed(() => route.hash.slice(1));
const anchorExists = computed(
  () => !anchor.value || currentDocument.value?.anchors.includes(anchor.value),
);

function documentUrl(id: string, heading = ''): string {
  return `/#/usage-docs?doc=${encodeURIComponent(id)}${heading ? `#${encodeURIComponent(heading)}` : ''}`;
}

function navigate(event: MouseEvent): void {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  )
    return;
  if (!(event.target instanceof Element)) return;
  const link = event.target.closest('a');
  const href = link?.getAttribute('href');
  if (!href?.startsWith('/#/usage-docs?')) return;
  event.preventDefault();
  navigationOpen.value = false;
  if (href === documentUrl(currentDocument.value!.id, anchor.value)) {
    scrollToSection();
  } else {
    void chunkLoadRecovery.push(href.slice(2));
  }
}

function scrollToSection(): void {
  if (!article.value || !anchorExists.value) return;
  const target = anchor.value
    ? [...article.value.querySelectorAll<HTMLElement>('[id]')].find(
        (element) => element.id === anchor.value,
      )!
    : article.value;
  target.scrollIntoView({ block: 'start' });
}

onMounted(scrollToSection);
watch([currentDocument, anchor], scrollToSection, { flush: 'post' });
</script>

<template>
  <div class="usage-docs" @click="navigate">
    <template v-if="currentDocument">
      <aside class="usage-sidebar" :class="{ 'is-open': navigationOpen }">
        <button
          type="button"
          class="usage-navigation-toggle"
          :aria-expanded="navigationOpen"
          aria-controls="usage-navigation"
          @click="navigationOpen = !navigationOpen"
        >
          <BookOpen :size="18" />
          文档与章节
          <ChevronDown :size="16" />
        </button>
        <div id="usage-navigation" class="usage-sidebar-body">
          <nav aria-label="文档导航">
            <h2 class="usage-nav-title">使用文档</h2>
            <a
              v-for="document in documents"
              :key="document.id"
              :href="documentUrl(document.id)"
              :aria-current="document.id === currentDocument.id ? 'page' : undefined"
              >{{ document.id === 'README.md' ? '项目概览' : document.title }}</a
            >
          </nav>
          <nav v-if="currentDocument.headings.length" aria-label="章节目录" class="usage-toc">
            <h2 class="usage-nav-title">本篇目录</h2>
            <a
              v-for="heading in currentDocument.headings"
              :key="heading.id"
              :href="documentUrl(currentDocument.id, heading.id)"
              :aria-current="anchor === heading.id ? 'location' : undefined"
              :class="{ 'usage-subheading': heading.level > 2 }"
              >{{ heading.text }}</a
            >
          </nav>
        </div>
      </aside>
      <div class="usage-content">
        <CAlert v-if="!anchorExists" type="warning" title="未找到指定章节" class="mb-5">
          可以从本篇目录重新选择章节。
        </CAlert>
        <article
          ref="article"
          class="usage-prose"
          :aria-label="currentDocument.title"
          v-html="currentDocument.html"
        />
      </div>
    </template>
    <div v-else class="space-y-4">
      <CAlert type="error" title="文档不存在">请检查文档链接，或返回使用文档首页。</CAlert>
      <CButton @click="chunkLoadRecovery.push('/usage-docs')">返回文档首页</CButton>
    </div>
  </div>
</template>

<style scoped>
.usage-docs {
  display: grid;
  gap: 1.5rem;
  min-width: 0;
}
.usage-sidebar {
  min-width: 0;
}
.usage-navigation-toggle {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 0.75rem;
  background: var(--color-surface);
  color: var(--color-text);
}
.usage-navigation-toggle svg:last-child {
  margin-left: auto;
}
.usage-sidebar-body {
  display: none;
  padding: 1rem;
  border: 1px solid var(--color-border);
  border-radius: 0.75rem;
  background: var(--color-surface);
}
.is-open .usage-sidebar-body {
  display: block;
  margin-top: 0.75rem;
}
.usage-nav-title {
  margin-bottom: 0.65rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-muted);
}
.usage-sidebar nav a {
  display: block;
  padding: 0.45rem 0.65rem;
  border-radius: 0.375rem;
  color: var(--color-muted);
  font-size: 0.8125rem;
  line-height: 1.5;
  overflow-wrap: anywhere;
  transition:
    color var(--duration-fast),
    background-color var(--duration-fast);
}
.usage-sidebar nav a:hover {
  background: var(--color-surface-2);
  color: var(--color-text-strong);
}
.usage-sidebar nav a[aria-current] {
  background: var(--color-surface-2);
  color: var(--color-tone-brand);
  font-weight: 600;
}
.usage-sidebar nav a.usage-subheading {
  padding-left: 1.4rem;
  font-size: 0.75rem;
}
.usage-toc {
  margin-top: 1.5rem;
  border-top: 1px solid var(--color-border);
  padding-top: 1.25rem;
}
.usage-content {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: 0.875rem;
  padding: 1.25rem;
  background: var(--color-surface);
}
.usage-prose {
  color: var(--color-text);
  font-size: 0.875rem;
  line-height: 1.85;
  overflow-wrap: anywhere;
  scroll-margin-top: 6rem;
}
.usage-prose :deep([id]) {
  scroll-margin-top: 6rem;
}
.usage-prose :deep(h1),
.usage-prose :deep(h2),
.usage-prose :deep(h3),
.usage-prose :deep(h4),
.usage-prose :deep(h5),
.usage-prose :deep(h6) {
  color: var(--color-text-strong);
  font-weight: 650;
  line-height: 1.4;
  margin: 1.8em 0 0.8em;
}
.usage-prose :deep(h1) {
  font-size: 1.75rem;
  margin-top: 0;
}
.usage-prose :deep(h2) {
  font-size: 1.3rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--color-border);
}
.usage-prose :deep(h3) {
  font-size: 1.1rem;
}
.usage-prose :deep(p),
.usage-prose :deep(ul),
.usage-prose :deep(ol),
.usage-prose :deep(pre),
.usage-prose :deep(blockquote),
.usage-prose :deep(table) {
  margin: 1em 0;
}
.usage-prose :deep(ul) {
  list-style: disc;
  padding-left: 1.5rem;
}
.usage-prose :deep(ol) {
  list-style: decimal;
  padding-left: 1.5rem;
}
.usage-prose :deep(li) {
  margin: 0.3em 0;
}
.usage-prose :deep(a) {
  color: var(--color-tone-brand);
  text-decoration: underline;
  text-underline-offset: 0.2em;
}
.usage-prose :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.86em;
  padding: 0.15em 0.35em;
  border-radius: 0.3em;
  background: var(--color-surface-2);
}
.usage-prose :deep(pre) {
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid var(--color-border);
  border-radius: 0.6rem;
  padding: 1rem;
  background: var(--color-surface-2);
  line-height: 1.65;
  overflow-wrap: normal;
}
.usage-prose :deep(pre code) {
  padding: 0;
  background: transparent;
}
.usage-prose :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}
.usage-prose :deep(th),
.usage-prose :deep(td) {
  padding: 0.6rem 0.8rem;
  border: 1px solid var(--color-border);
  text-align: left;
  min-width: 6rem;
}
.usage-prose :deep(th) {
  font-weight: 600;
  background: var(--color-surface-2);
}
.usage-prose :deep(blockquote) {
  padding: 0.15rem 1rem;
  border-left: 3px solid var(--color-border);
  color: var(--color-muted);
}
.usage-prose :deep(.usage-note) {
  border-color: var(--color-tone-brand);
  background: var(--color-surface-2);
  border-radius: 0 0.5rem 0.5rem 0;
}
.usage-prose :deep(img) {
  max-width: 100%;
  height: auto;
}
.usage-prose :deep(h1 img) {
  display: inline-block;
  width: 3em;
  height: 3em;
}
.usage-prose :deep(.usage-center) {
  text-align: center;
}
.usage-prose :deep(hr) {
  margin: 2rem 0;
  border-color: var(--color-border);
}
@media (min-width: 64rem) {
  .usage-docs {
    grid-template-columns: 14rem minmax(0, 1fr);
    align-items: start;
  }
  .usage-navigation-toggle {
    display: none;
  }
  .usage-sidebar {
    position: sticky;
    top: 6rem;
    max-height: calc(100vh - 7rem);
    overflow-y: auto;
  }
  .usage-sidebar-body,
  .is-open .usage-sidebar-body {
    display: block;
    margin-top: 0;
  }
  .usage-content {
    padding: 2rem;
  }
}
</style>
