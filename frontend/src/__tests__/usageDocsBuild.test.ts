import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { buildUsageDocs } from '../../tooling/usageDocs';

let root: string;
function write(path: string, content: string) {
  const target = join(root, path);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, content);
}
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'usage-docs-test-'));
  write('README.md', '# 项目\n\n[指南](doc/指南.md#章节)');
  write('doc/指南.md', '# 指南\n\n## 章节\n\n[首页](../README.md)');
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

describe('使用文档编译', () => {
  it('自动收录嵌套文档，保留中文、重复标题、显式锚点和站内链接', () => {
    write(
      'doc/子目录/补充.md',
      '# 补充\n\n## 章节\n\n## 章节\n\n<a id="custom"></a>正文\n\n[定位](#custom)',
    );
    const result = buildUsageDocs(root);
    expect(result.documents.map((doc) => doc.id)).toEqual([
      'README.md',
      'doc/子目录/补充.md',
      'doc/指南.md',
    ]);
    expect(result.documents[0]!.html).toContain(
      '/#/usage-docs?doc=doc%2F%E6%8C%87%E5%8D%97.md#%E7%AB%A0%E8%8A%82',
    );
    expect(result.documents[1]!.anchors).toEqual(
      expect.arrayContaining(['章节', '章节-1', 'custom']),
    );
    expect(result.documents[1]!.headings).toEqual([
      { id: '章节', text: '章节', level: 2 },
      { id: '章节-1', text: '章节', level: 2 },
    ]);
    expect(result.documents[2]!.html).toContain('doc=README.md');
  });

  it('渲染现有语法并清洗可执行 HTML，外部链接在新标签页打开', () => {
    write(
      'README.md',
      '<h1 align="center">项目</h1>\n\n| 名称 | 值 |\n| --- | --- |\n| 参数 | 1 |\n\n```bash\necho "<test>"\n```\n\n> [!NOTE]\n> 提示内容\n\n<script>alert(1)</script><img src="javascript:alert(1)" onerror="alert(2)"><iframe src="https://example.com"></iframe>\n\n[外链](https://example.com)',
    );
    const html = buildUsageDocs(root).documents[0]!.html;
    expect(html).toContain('<table>');
    expect(html).toContain('language-bash');
    expect(html).toContain('&lt;test&gt;');
    expect(html).toContain('usage-note');
    expect(html).toContain('提示内容');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).not.toMatch(/<script|onerror|javascript:|<iframe/);
  });

  it('图片和公开附件同源发布，哈希稳定且随内容变化，下载保留文件名', () => {
    write(
      'README.md',
      '# 项目\n\n![图标](frontend/public/assets/codebuddy2api.svg)\n\n[配置](doc/示例.json)\n\n[环境](.env.example)',
    );
    write('frontend/public/assets/codebuddy2api.svg', '<svg/>');
    write('doc/示例.json', '{}');
    write('.env.example', 'KEY=value');
    const first = buildUsageDocs(root);
    expect(first.assets).toHaveLength(3);
    expect(first.documents[0]!.html).toContain('download="示例.json"');
    expect(first.documents[0]!.html).toMatch(/src="\/assets\/usage-[a-f0-9]{12}\.svg"/);
    expect(buildUsageDocs(root).assets).toEqual(first.assets);
    write('doc/示例.json', '{"changed":true}');
    expect(buildUsageDocs(root).assets).not.toEqual(first.assets);
  });

  it.each([
    ['[失效](doc/missing.md)', '不存在'],
    ['[失效](doc/指南.md#missing)', '锚点'],
    ['[越界](../secret.txt)', '越界'],
    ['[研发](docs/内部.md)', '公开'],
    ['[配置](config.py)', '公开'],
  ])('拒绝无效内部引用：%s', (link, error) => {
    write('README.md', `# 项目\n\n${link}`);
    expect(() => buildUsageDocs(root)).toThrow(error);
  });

  it('拒绝文档目录内的符号链接', () => {
    symlinkSync(join(root, 'README.md'), join(root, 'doc/link.md'));
    expect(() => buildUsageDocs(root)).toThrow('符号链接');
  });

  it('拒绝非普通文档文件和目录形式的附件', () => {
    execFileSync('mkfifo', [join(root, 'doc/pipe')]);
    expect(() => buildUsageDocs(root)).toThrow('普通文件');
    rmSync(join(root, 'doc/pipe'));
    write('README.md', '# 项目\n\n[附件](.env.example)');
    mkdirSync(join(root, '.env.example'));
    expect(() => buildUsageDocs(root)).toThrow('普通文件');
  });

  it('拒绝缺失标题、空标题和重复或空锚点', () => {
    for (const content of [
      '正文',
      '# ',
      '# 标题\n\n<a id="x"></a><a id="x"></a>',
      '# 标题\n\n<a id=""></a>',
    ]) {
      write('README.md', content);
      expect(() => buildUsageDocs(root)).toThrow(/标题|锚点/);
    }
  });

  it('保留显式标题 ID，并避免生成的锚点与显式锚点冲突', () => {
    write('README.md', '<h1 id="project">项目</h1>\n\n<a id="章节"></a>\n\n## 章节\n\n> 普通引用');
    const document = buildUsageDocs(root).documents[0]!;
    expect(document.anchors).toEqual(['project', '章节', '章节-1']);
    expect(document.html).not.toContain('usage-note');
  });

  it.each([
    ['![图片](https://example.com/image.png)', '本地资源'],
    ['[根路径](/README.md)', '相对路径'],
    ['[反斜线](doc%5C指南.md)', '相对路径'],
    ['[查询](README.md?x=1)', '相对路径'],
    ['[附件](doc/example.json#section)', '附件不支持'],
    ['[附件](doc/example.bin)', '附件类型'],
  ])('拒绝无法离线转换的引用：%s', (link, error) => {
    write('README.md', `# 项目\n\n${link}`);
    write('doc/example.bin', 'binary');
    expect(() => buildUsageDocs(root)).toThrow(error);
  });

  it('无扩展名的公开附件以文本发布，多次引用复用同一个资源', () => {
    write('README.md', '# 项目\n\n[许可](LICENSE)\n\n[许可副本](LICENSE)');
    write('LICENSE', 'license');
    const result = buildUsageDocs(root);
    expect(result.assets).toHaveLength(1);
    expect(result.assets[0]!.fileName).toMatch(/\.txt$/);
  });

  it('编译仓库实际文档，所有内部链接都可解析', () => {
    const result = buildUsageDocs(join(import.meta.dirname, '../../..'));
    expect(result.documents).toHaveLength(6);
    expect(result.documents.some((doc) => doc.id === 'doc/Codex接入.md')).toBe(true);
    expect(result.assets.some((asset) => asset.fileName.endsWith('.json'))).toBe(true);
  });
});
