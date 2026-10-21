import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync } from 'node:fs';
import { basename, extname, join, posix, relative, resolve, sep } from 'node:path';
import { marked } from 'marked';
import createDOMPurify from 'dompurify';
import GithubSlugger from 'github-slugger';
import { JSDOM } from 'jsdom';
import type { UsageDocument } from '../src/types/usageDocs.ts';

export const PUBLIC_ATTACHMENTS = [
  'LICENSE',
  'LICENSING.md',
  'docker-compose.yml',
  '.env.example',
  'src/tokenizer_catalog.json',
  'frontend/public/assets/codebuddy2api.svg',
];

const MEDIA_TYPES: Record<string, string> = {
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.json': 'application/json',
  '.md': 'text/plain; charset=utf-8',
  '.yml': 'text/plain; charset=utf-8',
  '.yaml': 'text/plain; charset=utf-8',
  '.example': 'text/plain; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
};

export interface DocumentationAsset {
  fileName: string;
  source: Buffer;
  mediaType: string;
}

export interface UsageDocsBuild {
  documents: UsageDocument[];
  assets: DocumentationAsset[];
  files: string[];
}

/** 仅从确定的公开输入读取普通文件，不跟随任何一层符号链接。 */
function checkedPath(root: string, path: string): string {
  const absolute = resolve(root, path);
  const local = relative(root, absolute);
  let current = root;
  for (const part of local.split(sep)) {
    current = join(current, part);
    if (lstatSync(current).isSymbolicLink()) {
      throw new Error(`文档输入不能是符号链接：${path}`);
    }
  }
  return absolute;
}

export function buildUsageDocs(repositoryRoot: string): UsageDocsBuild {
  const root = resolve(repositoryRoot);
  const files = new Set<string>();
  const assets = new Map<string, DocumentationAsset>();
  const sources = ['README.md'];
  const window = new JSDOM('').window;
  const purify = createDOMPurify(window);
  const sanitize = (html: string) =>
    purify.sanitize(html, {
      USE_PROFILES: { html: true },
      ADD_ATTR: ['target', 'download'],
      FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'object', 'embed'],
      FORBID_ATTR: ['style', 'srcset'],
      ALLOW_DATA_ATTR: false,
    });

  function discover(directory: string) {
    for (const name of readdirSync(checkedPath(root, directory)).sort()) {
      const path = `${directory}/${name}`;
      const stat = lstatSync(checkedPath(root, path));
      if (stat.isDirectory()) discover(path);
      else if (!stat.isFile()) throw new Error(`文档输入必须是普通文件：${path}`);
      else if (path.endsWith('.md')) sources.push(path);
    }
  }

  function read(path: string): Buffer {
    const absolute = checkedPath(root, path);
    if (!lstatSync(absolute).isFile()) throw new Error(`文档输入必须是普通文件：${path}`);
    files.add(absolute);
    return readFileSync(absolute);
  }

  function assetUrl(path: string): string {
    const source = read(path);
    const extension = extname(path) || '.txt';
    const mediaType = MEDIA_TYPES[extension];
    if (!mediaType) throw new Error(`不支持的文档附件类型：${path}`);
    const hash = createHash('sha256').update(source).digest('hex').slice(0, 12);
    const fileName = `assets/usage-${hash}${extension}`;
    assets.set(fileName, { fileName, source, mediaType });
    return `/${fileName}`;
  }

  try {
    discover('doc');
    const parsed = sources.map((id) => {
      const body = window.document.createElement('article');
      body.innerHTML = sanitize(marked.parse(read(id).toString('utf8'), { async: false }));
      const title = body.querySelector('h1')?.textContent.trim();
      if (!title) throw new Error(`文档缺少一级标题：${id}`);
      const anchors = new Set<string>();
      for (const node of body.querySelectorAll('[id]')) {
        if (!node.id || anchors.has(node.id)) throw new Error(`文档锚点重复或为空：${id}`);
        anchors.add(node.id);
      }
      const slugger = new GithubSlugger();
      const headings: UsageDocument['headings'] = [];
      for (const node of body.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
        const text = node.textContent.trim();
        if (!node.id) {
          let anchor = slugger.slug(text);
          while (anchors.has(anchor)) anchor = slugger.slug(text);
          node.id = anchor;
          anchors.add(anchor);
        }
        const level = Number(node.tagName[1]);
        if (level > 1) headings.push({ id: node.id, text, level });
      }
      for (const node of body.querySelectorAll('[align="center"]')) {
        node.classList.add('usage-center');
        node.removeAttribute('align');
      }
      for (const quote of body.querySelectorAll('blockquote')) {
        const paragraph = quote.querySelector('p');
        if (paragraph && /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]/.test(paragraph.textContent)) {
          quote.classList.add('usage-note');
        }
      }
      return { id, title, body, headings, anchors };
    });

    for (const doc of parsed) {
      for (const element of doc.body.querySelectorAll('a[href],img[src]')) {
        const image = element.tagName === 'IMG';
        const attribute = image ? 'src' : 'href';
        const url = element.getAttribute(attribute)!;
        if (/^(?:https?:|mailto:|\/\/)/i.test(url)) {
          if (image) throw new Error(`文档图片必须使用本地资源：${doc.id}`);
          element.setAttribute('target', '_blank');
          element.setAttribute('rel', 'noopener noreferrer');
          continue;
        }
        const hashIndex = url.indexOf('#');
        const rawPath = hashIndex < 0 ? url : url.slice(0, hashIndex);
        const anchor = hashIndex < 0 ? '' : decodeURIComponent(url.slice(hashIndex + 1));
        const decoded = decodeURIComponent(rawPath);
        if (decoded.startsWith('/') || decoded.includes('\\') || decoded.includes('?')) {
          throw new Error(`无效的文档相对路径：${url}`);
        }
        const target = decoded
          ? posix.normalize(posix.join(posix.dirname(doc.id), decoded))
          : doc.id;
        if (target === '..' || target.startsWith('../')) throw new Error(`文档引用越界：${url}`);
        const destination = parsed.find((item) => item.id === target);
        if (destination && !image) {
          if (anchor && !destination.anchors.has(anchor)) throw new Error(`文档锚点不存在：${url}`);
          element.setAttribute(
            'href',
            `/#/usage-docs?doc=${encodeURIComponent(target)}${anchor ? `#${encodeURIComponent(anchor)}` : ''}`,
          );
        } else {
          if (!target.startsWith('doc/') && !PUBLIC_ATTACHMENTS.includes(target)) {
            throw new Error(`文档仅允许引用公开附件：${target}`);
          }
          if (target.endsWith('.md') && target.startsWith('doc/'))
            throw new Error(`文档不存在：${target}`);
          if (anchor) throw new Error(`附件不支持章节锚点：${url}`);
          element.setAttribute(attribute, assetUrl(target));
          if (!image) element.setAttribute('download', basename(target));
        }
      }
    }
    return {
      documents: parsed.map(({ id, title, body, headings, anchors }) => ({
        id,
        title,
        html: sanitize(body.innerHTML),
        headings,
        anchors: [...anchors],
      })),
      assets: [...assets.values()],
      files: [...files],
    };
  } finally {
    window.close();
  }
}
