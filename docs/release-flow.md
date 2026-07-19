# GitHub 发布流程

GitHub 仓库保存源码、文档、测试、安全检查和可复现构建配置；Release 保存面向普通用户下载的 Portable 包。

## 推荐流程

1. 本地完成修改。
2. 运行发布前检查：

   ```powershell
   .venv\Scripts\python tools\release\check_release_ready.py
   ```

3. 提交源码和文档：

   ```powershell
   git add ...
   git commit -m "合适的中文说明"
   git push
   ```

4. 等待 GitHub Actions 在干净环境执行安全检查、测试和 Windows Portable 构建。
5. 使用 Actions 生成的 Portable ZIP 和 SHA256 作为 Release 资产。

## 本机构建的定位

本机构建只作为验证和临时兜底：

```powershell
tools\release\windows\build_portable_windows.ps1 -Python .venv\Scripts\python.exe
tools\release\windows\smoke_portable_windows.ps1 -Python .venv\Scripts\python.exe
```

正式 Release 优先使用 GitHub Actions 的干净构建结果，降低混入本地题库或环境差异的风险。

## 发布前检查脚本

`tools/release/check_release_ready.py` 是只读脚本，会检查：

- 工作区状态；
- 公开仓库安全检查；
- Python 语法；
- Ruff 正确性检查；
- 自动测试与至少 65% 的分支覆盖率；
- 运行时依赖漏洞审计；
- 390px 移动端、侧栏与关键页面的视觉结构契约；
- GitHub Actions 与 Portable 构建配置是否存在。

它不会提交、推送、创建 Release 或上传资产。
