# 我爱学习（I Love Learning）

面向个人长期学习的本地题库、复习与进度管理工具。它使用稳定的知识层级组织认证、课程和自学内容，数据默认保存在本机，并可通过可信家庭局域网在电脑和手机上使用。

<p align="center">
  <img src="docs/assets/desktop.jpg" width="68%" alt="我爱学习桌面端首页">
  <img src="docs/assets/mobile.jpg" width="27%" alt="我爱学习手机端首页">
</p>

## 核心功能

- 以 `学习项目 → 科目 → 章节 → 知识点 → 题目` 组织不同学习目标，进度互不影响。
- 支持单选、多选、判断、填空和简答，以及可暂停恢复的章节练习、错题复习、模拟考试、掌握度和间隔复习。
- 提供普通刷题、考试备考和实操认证三种项目模板，可选模拟考试、学习计划、实践任务和准备度。
- 支持 PDF、DOCX、XLSX、CSV 导入，以及可校验、可往返的 ZIP 题库分享包。
- 桌面端使用左侧导航，手机端采用响应式布局；同一局域网共享一份本地数据。
- 知识节点删除前先迁移内容，并保留题目 ID、学习进度和历史记录。
- 提供学习数据管理、操作前快照、健康检查和可恢复的会话清理。

## 运行要求

- Windows 10/11，或支持 Docker Engine 的 Ubuntu/Debian Linux
- Windows 本机运行需要 Python 3.11；Linux 推荐使用 Docker Compose
- 手机访问时，手机与电脑需连接同一可信局域网

## Windows 快速开始

1. 下载或克隆本仓库。
2. 双击 `start.bat`；首次运行会创建本地环境并安装所需依赖。
3. 电脑打开 `http://127.0.0.1:23456`，手机使用设置页显示的局域网地址。

运行期间电脑和启动窗口必须保持开启。软件没有账号与登录保护，**不要配置公网端口映射，也不要在不可信网络中运行**。

## Linux 部署

Linux 服务器使用单容器 Docker Compose 部署，数据和备份保存在宿主机绑定目录，并仅绑定服务器内网 IP。完整步骤见 [Linux Docker Compose 部署](docs/linux-deployment.md)。

Windows 与 Linux 实例默认完全独立，不共享 SQLite，也不会自动同步题库或学习进度。Linux 全新部署首次启动为空题库。

## 数据与备份

数据库、题目图片、导入原件和配置都保存在数据目录。“数据管理”页面可以创建完整快照；Windows 默认保存到 `%USERPROFILE%\Documents\I-Love-Learning-Backup`，Linux Docker 保存到配置的宿主机备份目录。恢复前先停止软件，再用快照中的完整 `data` 目录替换当前目录。归档项目不会删除内容；永久删除项目、非空知识节点和学习记录前都会自动备份。

公开仓库不会包含本机题库：`data`、`题库` 和 `backups` 均被 Git 忽略，全新克隆首次启动时会创建零题目的数据库和默认知识结构。

## 题库导入与分享

- 普通 PDF/DOCX 必须包含可复制的结构化文本，不支持 OCR；XLSX/CSV 使用页面提供的固定模板。
- 普通文档导入后统一进入草稿，人工核验后才会进入默认练习和模拟考试。
- ZIP 分享包可按项目、科目或章节导出，默认仅包含已核验题，不包含数据库 ID、来源资料和个人学习记录。
- ZIP 导入会先校验结构、文件哈希和压缩安全，再处理项目映射、重复题与审核状态。
- 分享包不加密，也不代表内容授权，请通过可信方式传递并自行确认分享权限。

<details>
<summary><strong>高级配置、公开提交与开发</strong></summary>

### 高级配置

支持 `STUDY_DATA_DIR`、`STUDY_DB_NAME`、`STUDY_BACKUP_DIR`、`STUDY_SECRET` 和 `STUDY_MAX_UPLOAD_MB`。未配置密钥时会在本地 `data` 中生成随机密钥；旧环境变量 `H3CSE_DATA_DIR`、`H3CSE_SECRET` 继续兼容。

运行日志保存在 `data/logs/app.log`，`/health` 提供只读数据库健康状态。SQLite 使用 WAL，导入和导出任务串行执行，适合个人电脑和家庭局域网使用。

### 公开提交检查

提交或推送前运行：

```powershell
check-public-repo.bat --no-pause
```

检查器只读取 Git 索引和历史，不扫描或修改本机 `data`。不要运行 `git clean -fdx`，因为 `-x` 会删除被忽略的本地学习数据。

### 开发与测试

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -v
```

</details>

领域术语见 [CONTEXT.md](CONTEXT.md)，关键取舍见 [关键设计决定](docs/decisions.md)。

## 许可证

当前仓库尚未附带开源许可证。除非另有书面许可，不授予复制、修改或再分发本项目代码的权利。

学习统计和准备度只用于个人学习决策，不代表任何机构的官方考试标准。
