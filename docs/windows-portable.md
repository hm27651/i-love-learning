# Windows Portable 使用与发布

Windows Portable 包适合给其他 Windows 10/11 电脑使用。目标电脑不需要安装 Python，也不需要克隆仓库。

## 使用

1. 解压 `I-Love-Learning-Portable.zip`。
2. 双击 `I-Love-Learning.exe`。
3. 首次启动时选择打开方式：
   - 软件内打开（GUI）：在启动器窗口中直接显示软件页面。
   - 浏览器打开（Web）：启动服务后用系统浏览器访问 `http://127.0.0.1:23456`。
4. 手机访问时，切换到“允许同一局域网访问”，并确保只在可信内网使用。

启动器会限制同一目录只打开一个实例，避免多个服务同时访问同一个 SQLite 数据库。
启动器会记住上次选择的访问范围和打开方式，但每次启动前都可以切换。启动器窗口会显示当前访问地址和数据目录；如果提示端口 `23456` 已被占用，请先关闭旧的源码服务、另一个 Portable 启动器或其它占用该端口的进程后再启动。

GUI 模式依赖系统 Microsoft Edge WebView2 Runtime。Windows 10/11 多数环境已预装；如果 GUI 初始化失败，请安装 WebView2 Runtime，或改用“浏览器打开（Web）”。

## 数据

Portable 数据固定保存在程序目录下的 `data`：

- `h3cse.db`：SQLite 数据库
- `uploads/`：题目和实践任务图片
- `imports/`、`exports/`：导入原件和导出分享包
- `logs/`：运行日志

升级时保留 `data`，只替换 `I-Love-Learning.exe` 和 `_internal`。不要把正在运行的数据库直接复制给别人；题库分享应使用“导入与导出”里的 ZIP 分享包。

## 构建

```powershell
tools\release\windows\build_portable_windows.ps1 -Python .venv\Scripts\python.exe
```

产物：

- `dist\I-Love-Learning-Portable.zip`
- `dist\I-Love-Learning-Portable.zip.sha256`

发布包默认只有空 `data` 目录，不包含本机题库、数据库、导入文档或备份。首次运行时应用会自动创建空数据库。

## 注意

- 当前不做安装器，不写注册表，不安装系统服务。
- 当前不做自动升级，手动替换程序文件并保留 `data`。
- EXE 未签名，Windows SmartScreen 可能提示风险，需要手动允许。
- 不要把端口映射到公网。
