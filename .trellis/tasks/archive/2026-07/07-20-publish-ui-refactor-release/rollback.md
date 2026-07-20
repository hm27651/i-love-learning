# v2.0 发布前回滚资料

记录时间：2026-07-20

## 标签与仓库

- 远端仓库：`hm27651/i-love-learning`
- 旧 GitHub Description：`面向个人长期学习的本地题库、练习、复习与进度管理平台`
- 旧 annotated tag 对象：`184cd4b5f29bec27d1390c5d3baef8d5bd8d7a5e`
- 旧标签解引用提交：`c5e025c54098d3b474d8af4887cc186d43808b02`

## Release 与资产

- Release ID：`354997410`
- 标题：`我爱学习 v2.0`
- 目标：`main`
- 旧资产 ID：`482521674`
- 旧资产名称：`I-Love-Learning-Portable.zip`
- 旧资产大小：`46296813` 字节
- 旧资产 SHA-256：`ECDCBDD5F613916B653E29A979B05CB0E8A0E71813DE05C3DEA26D20759DF38C`
- 旧资产下载次数：`2`
- 本地回滚副本：`tmp/release-rollback/I-Love-Learning-Portable-old.zip`（Git 忽略，不提交）

## 旧 Release 正文

## 我爱学习 v2.0

v2.0 将项目升级为通用的个人多项目学习平台，并提供可直接解压使用的 Windows Portable 版本。本次更新进一步完善首次使用体验、运行架构、数据迁移保护和自动化质量检查。

### 本次更新重点

- 新增首次运行向导，可选择普通刷题、考试备考或实操认证模板；已有题库不会被打断。
- 重构为 create_app 应用工厂，路由、数据目录、上传目录和后台队列按应用实例隔离。
- 数据库升级前自动创建完整快照，升级后检查 SQLite 完整性和外键。
- 修复首次空库健康检查，Portable 与 Docker 均可正确判断服务状态。
- 完善 Windows 启动器 GUI/Web 点击链路、日志和异常处理测试。
- 增加 390px 手机端、关键页面和侧栏视觉结构回归检查。
- 接入 Ruff、分支覆盖率、依赖漏洞审计和 GitHub Actions 质量门禁。
- 更新 Flask、Pillow 与 PDF 解析依赖，当前审计无已知漏洞。
- 修复 Windows Portable 导入 PDF 题库时报 `No module named 'tools'` 的问题，并将 PDF 解析器纳入正式运行模块。

### 主要功能

- 使用“学习项目 → 科目 → 章节 → 知识点 → 题目”管理不同学习目标。
- 支持章节练习、错题复习、模拟考试、暂停继续、学习计划和实践任务。
- 支持 PDF、DOCX、XLSX、CSV 导入，以及可往返的 ZIP 题库分享包。
- 支持 Windows Portable 与 Linux Docker Compose，两端使用独立本地数据。

### Windows Portable 使用方式

1. 下载 I-Love-Learning-Portable.zip。
2. 完整解压到一个固定目录，不要直接在压缩包内运行。
3. 双击 I-Love-Learning.exe。
4. 首次选择“软件内打开（GUI）”或“浏览器打开（Web）”。
5. 默认仅本机访问；需要手机访问时，可切换为可信局域网模式。
6. 全新空题库会进入首次设置向导。

### 升级与数据安全

升级前请停止旧程序并完整备份 data 目录。推荐把新版本解压到新目录，再在启动器中选择原数据目录；不要覆盖、删除或用空目录替换原 data。题库分享请使用软件内的“导入与导出”。

本工具没有账号与公网访问保护，仅适合个人电脑和可信局域网使用，请勿将端口映射到公网。
