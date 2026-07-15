# 公开仓库发布检查清单

本项目允许真实学习数据继续留在本机 `data` 目录，但 GitHub 仓库必须保持零题库、零导入原件和零个人学习记录。

## 首次公开前

1. 关闭 23456 服务，完整复制 `data` 到仓库外的 `%USERPROFILE%\Documents\I-Love-Learning-Backup\<时间戳>\data`。
2. 在备份数据库上运行 `PRAGMA integrity_check`，核对题目数量、来源文档数量、导入批次数量和原文件 SHA-256。
3. 重启软件，确认原数据库数量未发生变化。
4. 双击 `check-public-repo.bat`，或运行：

   ```powershell
   .venv\Scripts\python tools\check_public_repo.py
   .venv\Scripts\python -m unittest discover -s tests -v
   ```

5. 暂存代码后再次运行检查。检查器只读取 Git 索引和历史，不扫描或修改被忽略的 `data`。
6. 获取远端完整分支和标签，确认远端没有本地尚未审计的历史：

   ```powershell
   git fetch --prune --tags
   .venv\Scripts\python tools\check_public_repo.py
   ```

7. 使用普通提交和普通推送。不要使用强制推送，除非后续审计明确发现旧历史泄漏并另行制定方案。

## 禁止操作

- 不要使用 `git add -f` 绕过忽略规则。
- 不要使用 `git clean -fdx`；参数 `-x` 会删除包括 `data` 在内的忽略文件。
- 不要把数据库、题库文档、导入原件、题目图片或 `.env` 放入 Release 附件。
- 检查失败时只处理 Git 索引或历史，不要删除本机 `data`。

## 推送后验证

1. 在临时目录重新克隆公开仓库，并运行公开检查和完整测试。
2. 首次启动后确认 `questions`、`source_documents` 和 `import_jobs` 均为 0。
3. 回到原工作目录，确认原数据库题目数和完整性不变。

GitHub Actions 会在每次推送和 Pull Request 中使用完整历史重复执行相同检查。
