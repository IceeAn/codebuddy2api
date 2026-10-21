import { resolve } from 'node:path';
import { buildUsageDocs } from '../../tooling/usageDocs';

// 单元测试使用真实编译内容；Vite 插件的完整集成由浏览器构建验证。
export default buildUsageDocs(resolve(import.meta.dirname, '../../..')).documents;
