# Windows Portable 使用与发布

Windows Portable 包适合给其他 Windows 10/11 电脑使用。目标电脑不需要安装 Python，也不需要克隆仓库。

## 使用

1. 解压 `I-Love-Learning-Portable.zip`。
2. 双击 `I-Love-Learning.exe`。
3. 首次启动时选择打开方式：
   - 软件内打开（GUI）：在启动器窗口中直接显示软件页面。
   - 浏览器打开（Web）：启动服务后用系统浏览器访问 `http://127.0.0.1:23456`。
4. 确认数据目录。已有题库时选择原完整 `data` 目录；新用户可使用默认目录。
5. 手机访问时，切换到“允许同一局域网访问”，并确保只在可信内网使用。

启动器会限制同一目录只打开一个实例，避免多个服务同时访问同一个 SQLite 数据库。
启动器会记住访问范围、打开方式和数据目录，配置保存在程序目录的 `.portable-launcher.json`。切换到空目录时会先显示原数据题数并询问是否迁移；迁移使用 SQLite 安全副本，原目录不会删除。启动器窗口会显示当前访问地址、数据目录、版本和启动日志位置；如果提示端口 `23456` 已被占用，请先关闭旧服务后再启动。

如果启动器或 GUI 接口异常，点击“重新载入接口”重试。诊断信息保存在 `data/logs/launcher.log`，应用请求与运行错误保存在 `data/logs/app.log`；两类日志都会滚动保留，不包含题目、答案或登录信息。

GUI 模式依赖系统 Microsoft Edge WebView2 Runtime。Windows 10/11 多数环境已预装；如果 GUI 初始化失败，请安装 WebView2 Runtime，或改用“浏览器打开（Web）”。

## 数据

Portable 数据默认保存在程序目录下的 `data`，也可以选择独立目录：

- `h3cse.db`：SQLite 数据库
- `uploads/`：题目和实践任务图片
- `imports/`、`exports/`：导入原件和导出分享包
- `logs/`：运行日志

升级时保留实际数据目录和 `.portable-launcher.json`，只替换 `I-Love-Learning.exe` 和 `_internal`。不要把正在运行的数据库直接复制给别人；题库分享应使用“导入与导出”里的 ZIP 分享包。

## 构建

```powershell
tools\release\windows\build_portable_windows.ps1 -Python .venv\Scripts\python.exe
```

产物：

- `dist\I-Love-Learning-Portable.zip`
- `dist\I-Love-Learning-Portable.zip.sha256`

发布包默认只有空 `data` 目录，不包含本机题库、数据库、导入文档或备份。首次运行时应用会自动创建空数据库。

构建解包目录位于 `build/portable-staging`，`dist` 只保存 ZIP 和 SHA-256。不要把构建目录当作正式安装目录；正式使用的程序应解压到 `D:\学习` 等独立位置。

## 注意

- 当前不做安装器，不写注册表，不安装系统服务。
- 当前不做自动升级，手动替换程序文件并保留 `data`。
- EXE 未签名，Windows SmartScreen 可能提示风险，需要手动允许。
- 不要把端口映射到公网。
