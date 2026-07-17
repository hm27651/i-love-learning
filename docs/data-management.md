# 数据目录管理

“我爱学习”的题库、图片、导入原件、导出包和配置都属于本地私有数据，不进入公开 GitHub 仓库。

## 数据位置原则

- Windows Portable：正式数据保存在 `I-Love-Learning.exe` 同级的 `data/`。
- Linux Docker：正式数据保存在宿主机绑定目录，例如 `/srv/i-love-learning/data`。
- 源码开发：仅用于调试。当前仓库内 `data/` 作为旧兼容目录保留，不自动迁移、不删除。

软件的“数据管理”页面会显示当前实际使用的数据目录、数据库、上传图片、导入原件、导出分享包和备份目录。

## 备份与恢复

备份使用“数据管理 → 立即完整备份”。恢复时固定按以下顺序：

1. 停止软件或容器。
2. 保存当前 `data/`，避免误覆盖后无法回退。
3. 用备份中的完整 `data/` 替换当前数据目录。
4. 启动软件并检查 `/health`。

不要在软件运行时直接复制 SQLite 数据库文件给别人。题库分享请使用软件内“导入与导出”的 ZIP 分享包。

## GitHub 保护线

以下内容只留在本机或服务器，不能提交：

- `data/`
- `backups/`
- `题库/`
- `build/`
- `dist/`
- SQLite 数据库、PDF、VCE、Office、CSV、图片和 `.env`

提交或发布前运行：

```powershell
.venv\Scripts\python tools\release\check_release_ready.py
```
