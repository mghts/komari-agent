# Komari Agent 协作指南

## 开始工作

- 使用简体中文沟通；代码标识符、命令、日志、配置项和文件名保持原文。
- 先阅读 `FORK.md`、相关源码和脚本，检查 `git status --short --branch`、remote 和已有差异，保留用户改动。
- 询问、方案和审阅任务先回答问题；实现或修复任务持续完成已授权工作与必要验证。保持修改集中，不顺手重构、升级依赖或改变默认行为。
- 区分文件证据、测试结果和推断；外部版本及 API 信息需要核查，无法验证的场景需明确说明。

## 仓库与源码基线

- 本仓库为 `mghts/komari-agent`，开发分支 `komari-agent-1.2.60`，上游源码基线固定为 `1.2.60`；准确提交见 `build/baseline.json`。
- 配套 Server/前端源码基线为 `1.4.3`，仓库为 `mghts/komari` 和 `mghts/komari-web`，开发分支均为 `komari-1.4.3`。
- 不因已有节点运行上游 `1.5.x` 而切换本仓库基线，也不自动合并上游后续版本。保留许可、作者信息和历史 tags；本 fork 的发行号与上游源码基线分别记录。
- 三个仓库独立提交和推送。当前本机本仓库位于 Server checkout 的 `.local/komari-agent`；新克隆或 worktree 不保证带有该路径，跨仓库操作先核对 checkout、remote，并阅读目标仓库的 `AGENTS.md`。
- 保留 Go module `github.com/komari-monitor/komari-agent`；不因 fork 名称不同全局替换 import 路径。

## 运行与升级约定

- 发行目标为 Linux `amd64`、`arm64`，同时提供 Docker 镜像和 systemd 安装所需二进制。
- 默认禁用自动更新；显式启用时更新源为 `mghts/komari-agent` 的正式 Release。RC 不作为自动升级目标。
- 容器内必须跳过二进制自更新，通过更换镜像升级；保持 `--version` 可查询，并支持仅用环境变量启动容器。
- 当前配置覆盖顺序是 JSON 文件高于环境变量，环境变量高于命令行。涉及配置解析时验证布尔值 `false` 能覆盖默认 `true`，并检查已有配置的兼容性。
- Docker Compose 默认关闭网页终端和远程命令；开启后操作对象是容器。宿主机终端需求使用 systemd 部署；宿主机指标、多磁盘、网络权限要在实际 Linux VPS 验收。
- 安装器 `install.sh` 使用明确版本、校验和与版本检查，全部成功后才停止旧服务。升级时保留现有 unit、参数、配置及 token，保留旧二进制备份；启动失败时尝试恢复。
- 修改安装器必须检查成功升级、下载损坏不停止服务、启动失败恢复旧二进制等路径。`scripts/upstream-install.sh` 是历史参考，不作为本 fork 的安装入口。

## 构建与验证

- 使用 Dockerfile 固定的 Go 工具链构建，Agent 使用 `CGO_ENABLED=0`。按变更选择必要检查：

```bash
python3 scripts/test_installer.py
docker build --target test -t komari-agent-tests:local .
docker build --build-arg VERSION=0.0.0-dev --build-arg REVISION="$(git rev-parse HEAD)" -t komari-agent:test .
bash scripts/smoke.sh komari-agent:test 0.0.0-dev
```

- Shell 脚本改动检查 `bash -n`；仅文档变更核对内容、路径和 `git diff --check`，不要求重跑完整构建。
- 默认探测测试使用本地 TCP/HTTP/ICMP 回环。公网测试需显式设置 `KOMARI_EXTERNAL_PING_TESTS=1`，ICMP 需要相应权限；不要把外部网络不可达直接归因于实现错误。
- 协议、指标、鉴权或远程任务变更需与配套 Server 联调。在 Server 仓库可用 `KOMARI_SMOKE_AGENT_IMAGE=komari-agent:test python3 scripts/smoke.py <server-image> <server-version>`；替换为实际测试镜像及其版本，使用隔离数据。
- 容器测试和模拟安装器测试不能代替实际 systemd 升降级及 VPS 运行验收。

## 发布与跨仓库衔接

- 使用 `.github/workflows/release.yml` 的 `Publish release` 和未使用的版本号，两种架构测试通过后发布同一批产物。
- 产物包括 `ghcr.io/mghts/komari-agent:<version>`、两个架构的二进制、`install.sh`、`SHA256SUMS`、`build-manifest.json`；RC 保持 Pre-release，不更新 `latest`，不覆盖已有版本。
- 配套版本变化时检查 Server 的 `build/agent.json` 和前端安装命令，并更新对应的明确版本、提交或 digest，验证组合兼容性。
- 部署示例见 `deploy/`，迁移、备份和回退步骤见 `FORK.md`。systemd 与 Docker Agent 不得同时使用同一节点 token 连接。
- 发布状态及 VPS 状态需独立核对；发布成功不代表已经部署。最新发行版和验证证据以 Git、Release、构建清单及实际环境为准，不在本文件积累阶段日志。

## 文件与数据保护

- 未经用户逐次明确授权，不执行批量或递归删除、清空目录、删除任何目录或一次删除多个文件；包括脚本和工具执行的删除。禁止用 `rm -rf`、通配符删除、覆盖、清空或强制重置规避限制。
- 确需删除时先列出确切对象、原因、影响和恢复方式，授权后确认路径及范围，优先采用可恢复方式；安装备份、下载缓存和测试数据也不自行清理。
- 不提交或输出真实 token、密码、配置中的凭据及生产数据。测试使用临时凭据，错误信息和命令日志避免泄露敏感参数。
