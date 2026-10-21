/** 构建期生成的公开文档，浏览器只读取编译后的内容。 */
export interface UsageDocument {
  id: string;
  title: string;
  html: string;
  headings: { id: string; text: string; level: number }[];
  anchors: string[];
}
