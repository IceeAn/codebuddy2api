# Licensing

## Current Code

The current working tree, starting from commit `bce86ded9ca59b6b99f5592621be21bce96b5ba3`, is licensed under the MIT License, to the extent authored by An!.

## Historical Upstream Content

This repository is a fork and preserves earlier Git history for attribution and transparency.

Historical commits before `bce86ded9ca59b6b99f5592621be21bce96b5ba3` may contain code, documentation, or other materials authored by the original upstream project owner or other contributors. Those historical materials are not licensed by this fork's maintainer under the MIT License.

## Excluded Materials

The MIT License does not apply to upstream code, third-party code, assets, artwork, documentation, or other materials not authored by this fork's maintainer, including any such materials that may appear in Git history.

## License File

The `LICENSE` file applies only to the current independently rewritten code authored by this fork's maintainer.
## Tokenizer 数据与编码格式

发布产物包含第三方官方分词数据，分别遵循各模型资源的许可证；来源仓库、固定 commit、数据文件及许可证文件的 SHA-256/大小均记录于 `src/tokenizer_catalog.json`。实际许可证原文与数据一起保存在 `src/tokenizer_assets/blobs/<SHA-256>`，可按清单中的 `LICENSE` 或 `LICENSE-MODEL` 查找。它们不因随本项目分发而改变原许可证。

DeepSeek V4/V4.1、Kimi K2/K3 的消息序列化实现和参考测试基于清单对应官方版本的编码格式；模型协议中的提示词字面量保持原文。下载脚本不执行这些仓库的远程代码。上传资源的来源及许可证由上传者管理。
