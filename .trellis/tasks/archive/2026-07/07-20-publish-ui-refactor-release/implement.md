# 发布全局 UI 重构版本：实施计划

## 1. 发布内容收尾

- [x] 更新 README 首屏定位、界面说明与核心能力，避免重复后续章节。
- [x] 编写最终 GitHub Description 和 `v2.0` Release 正文。
- [x] 保存旧标签目标、Release 正文、资产 ID 与旧摘要作为回滚资料。

## 2. 发布前验证

- [x] 运行 `.venv\Scripts\python tools\release\check_release_ready.py`（提交前使用 `--allow-dirty` 完整执行；提交后再运行无参数版本）。
- [x] 运行 `tools\release\windows\smoke_portable_windows.ps1 -Python .venv\Scripts\python.exe`。
- [x] 运行 `.venv\Scripts\python tools\safety\check_public_repo.py`。
- [x] 再次计算 `dist\I-Love-Learning-Portable.zip` SHA-256，结果为 `5BA14C9D7E014A50BEBD45B967811B650D85CE4C11736E75DD53F8375CD1E40D`。

## 3. 提交与推送

- [x] 检查暂存内容，确认不包含受保护数据或构建产物。
- [x] 使用中文提交说明提交 README 与发布任务记录。
- [x] 推送 `main`，验证 `origin/main` 与本地 `main` 一致。
- [x] 将 annotated tag `v2.0` 重建到发布提交并强制推送；任务归档后再对齐最终提交。

## 4. 重建现有 Release

- [x] 更新仓库 Description。
- [x] 更新现有 `v2.0` Release 标题与正文。
- [x] 删除旧 ZIP 资产并上传新版 ZIP；首次上传受 Chrome 文件权限拦截时撤销删除，授权后成功替换。
- [x] 确认 Release 的目标标签、标题、正文和资产名称正确。

## 5. 远端验收

- [x] 重新下载远端 ZIP 到临时目录并校验 SHA-256。
- [x] 通过 GitHub API 核对仓库 Description、Release 标签、资产大小和更新时间。
- [x] 检查 Git 工作区干净，记录最终提交和验证结果。
- [x] 运行 Trellis 完成检查、规范更新判断、任务归档与会话记录。

## 回滚点

- `main` 推送前：可直接修订本地提交，不影响远端。
- 标签移动后：使用保存的旧提交重建并强制推送 `v2.0`。
- Release 元数据更新后：使用保存的旧标题与正文恢复。
- 旧资产删除后：若新版上传失败，优先恢复旧资产，避免 Release 长期无下载文件。

## 发布结果

- 源码发布提交：`9163c8cedc7ab647c616bbd3241c13910678f53e`
- 新资产 ID：`483126273`
- 新资产大小：`46304904` 字节
- 新资产更新时间：`2026-07-20T06:07:41Z`
- 远端与本地 SHA-256：`5BA14C9D7E014A50BEBD45B967811B650D85CE4C11736E75DD53F8375CD1E40D`
- GitHub Description：`本地优先的多项目题库、间隔复习、模拟考试与学习进度平台`
- 规范更新判断：本任务未改变代码、接口、数据库或构建契约，无需修改 `.trellis/spec/`。
