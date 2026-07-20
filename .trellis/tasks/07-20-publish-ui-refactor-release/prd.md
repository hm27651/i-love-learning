# 发布全局 UI 重构版本

## Goal

将已完成并验证的全局 UI 重构版本推送到公开 GitHub 仓库，更新仓库项目介绍和 README，并让 GitHub Release 的源码指向、说明和 Windows Portable 资产保持一致。

## Background

- 远端仓库为 `hm27651/i-love-learning`，默认分支 `main`，当前公开描述为“面向个人长期学习的本地题库、练习、复习与进度管理平台”。
- 本地 `main` 比 `origin/main` 超前 4 个提交，`git push --dry-run` 已验证具备推送条件。
- 应用版本仍为 `2.0`；现有公开 Release 为 `v2.0`，资产 `I-Love-Learning-Portable.zip` 的旧摘要为 `ecdcbdd5...`。
- 当前已验证的新 Portable 包摘要为 `5BA14C9D7E014A50BEBD45B967811B650D85CE4C11736E75DD53F8375CD1E40D`。
- 本机没有 GitHub CLI；Git 推送可使用现有凭据，Release 元数据可通过 GitHub REST API读取。

## Requirements

- 更新 README 的项目开场、核心能力和界面说明，突出多项目学习、离线本地数据、GUI/Web 双模式、响应式设计及本次蓝靛 UI 重构，避免重复后续详细章节。
- 更新 GitHub 仓库 Description，使其与 README 的定位一致且保持一句话长度。
- 更新 Release 说明，覆盖本次全局 UI、导航、首页、专注答题、知识树、移动端可访问性、Trellis 与质量门禁。
- 上传新构建的 `I-Love-Learning-Portable.zip`，远端摘要必须与本地 SHA-256 一致；不上传本地题库、数据库或导入文件。
- 推送前重新执行发布门禁；推送和 Release 更新后通过 GitHub API 和重新下载资产验证远端状态。
- 保留现有中文说明风格，不增加在线字体、外部前端依赖或新的安装格式。
- 复用并重建现有 `v2.0` Release；将 annotated tag `v2.0` 移动到本次最终发布提交，不创建 `v2.1`。

## Acceptance Criteria

- [ ] `origin/main` 与本地 `main` 指向同一提交，GitHub 项目页显示新的简洁中文描述。
- [ ] README 首屏能清楚说明产品定位、运行方式和主要特色，且与实际功能一致。
- [ ] Release 标签、源码提交、标题、正文和 Portable 资产属于同一版本状态。
- [ ] 远端 ZIP 的 SHA-256 为 `5BA14C9D7E014A50BEBD45B967811B650D85CE4C11736E75DD53F8375CD1E40D`。
- [ ] 发布级检查、公开仓库检查和 Portable 烟测通过，Git 工作区最终干净。
- [ ] `v2.0^{}`、`origin/main` 与本地 `main` 最终指向同一提交，Release 仍使用标题“我爱学习 v2.0”。

## Out of Scope

- 不改变应用版本号、数据库结构、业务功能或导入导出格式。
- 不创建新标签、新 Release 或新的安装格式。
- 不提交、移动或修改本地学习数据、题库原件、备份与构建目录。
