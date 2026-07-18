我爱学习 Portable
==================

使用方法
1. 解压整个 I-Love-Learning-Portable 目录。
2. 双击 I-Love-Learning.exe。
3. 首次启动时选择打开方式：
   - 软件内打开（GUI）：在启动器窗口中直接显示软件页面。
   - 浏览器打开（Web）：用系统浏览器打开网页。
4. 默认使用“仅本机访问”；需要手机访问时，切换为“允许同一局域网访问”，并确保手机和电脑在同一个可信局域网。
5. 之后会记住上次选择，但每次启动前都可以切换。

数据位置
- 题库、图片、导入导出包和配置保存在本目录下的 data 文件夹。
- 升级时只替换 I-Love-Learning.exe 和 _internal，不要覆盖或删除 data。
- 分享题库请使用软件里的 ZIP 导入与导出功能，不要复制正在使用的 SQLite 数据库。

安全说明
- 本软件没有登录功能，只适合可信内网或本机使用。
- 不要把端口映射到公网。
- GUI 模式依赖系统 Microsoft Edge WebView2 Runtime；如果 GUI 初始化失败，可改用浏览器 Web 模式。
- 未签名 EXE 可能触发 Windows SmartScreen，首次运行时需要手动允许。

备份
- 页面里的“数据管理”可以创建完整备份。
- Windows 默认备份目录为 Documents\I-Love-Learning-Backup。
