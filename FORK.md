# Agent 基础发行版

基线为上游 `1.2.60`，提交 `8cd92149a845c12917e42acb1a296c836822758d`，配套 Server/前端 `1.4.3`。首个测试版本为 `1.2.61-rc.1`，并非采用上游 `1.2.61` 或 `1.5.x` 的代码。保留 MIT 许可和上游提交。

## 构建和发布

```bash
docker build --target test -t komari-agent-tests .
docker build --build-arg VERSION=0.0.0-dev --build-arg REVISION="$(git rev-parse HEAD)" -t komari-agent:test .
python3 scripts/test_installer.py
bash scripts/smoke.sh komari-agent:test 0.0.0-dev
```

GitHub Actions 的 `Publish release` 手动输入一个未使用的语义版本。两个原生 Linux runner 分别运行 Go 测试、镜像启动测试，再发布通过测试的同一批镜像与二进制文件。版本中有 `-` 时发布为 Pre-release，不更新 `latest`。不要覆盖旧版本。

产物：`ghcr.io/mghts/komari-agent:<version>`、Linux amd64/arm64 二进制、`install.sh`、`SHA256SUMS`、`build-manifest.json`。首次发布后核实 GHCR 包为 public。旧工作流移至 `.github/legacy-workflows`，原安装脚本保存在 `scripts/upstream-install.sh` 和 `scripts/upstream-install.ps1`，均不作为新发行版入口。

## 系统服务安装及首次迁移

仅支持 Linux/systemd、Python 3、amd64/arm64。Windows 不在发布矩阵内，根目录 `install.ps1` 在任何下载或服务变更前直接退出。先下载选定 Release 的 `install.sh`，再执行。可先加 `--dry-run`，只下载、校验及查询新程序版本。

新节点：

```bash
sudo bash install.sh --install-version 1.2.61 --endpoint https://monitor.example.com --token YOUR_NODE_TOKEN
```

已有一键安装节点，默认二进制是 `/opt/komari/agent`、服务名是 `komari-agent`。升级时省略 endpoint/token，保留已有 unit、参数及配置：

```bash
sudo bash install.sh --install-version 1.2.61
```

这也是从上游 `1.5.10` 切换到本 fork 的显式降级操作；执行前保存当前版本和配置，核实配套 Server。自定义路径/服务名须传入 `--install-dir`、`--install-service-name`。下载、SHA256、版本检查都成功后才会停止旧服务；旧二进制保留在安装目录的 `backup-<时间>` 中，启动失败时尝试恢复。备份和下载目录不自动删除。脚本确认服务启动后还需在面板检查节点上线。

本 fork 默认禁用自动更新；显式设置 `--disable-auto-update=false` 才订阅 `mghts/komari-agent` 的正式版本。JSON 配置优先于环境变量和命令行，已有配置若写了 false，需手动改为 true。RC 不会作为自动更新的目标。`agent --version` 可安全查询版本。

## Docker

复制 `deploy/compose.yaml` 和 `deploy/env.example` 到部署目录，将后者命名为 `.env` 并填写节点信息。设置 `.env` 权限为 600，然后运行：

```bash
docker compose config --quiet
docker compose pull
docker compose up -d
```

从 systemd 迁移时先停止旧 Agent，再启动容器，避免相同 token 同时连接两份 Agent。更新时修改 `.env` 的 `AGENT_VERSION` 并再次执行 pull/up；回退时改回旧标签。

Compose 使用 host 网络和 PID、只读宿主机挂载来采集主机数据；它适用于 Linux VPS，不能用 Docker Desktop 的虚拟机指标代替真实 VPS 验证。默认统计宿主机根文件系统，多个磁盘须增加挂载并调整 `AGENT_INCLUDE_MOUNTPOINTS`，避免重复统计。`state` 目录持久化流量统计状态。

容器内无论配置如何都跳过二进制自更新。容器模式默认关闭网页终端和远程命令；开启后操作目标是容器，不能当作宿主机终端。需要完整宿主机终端时保留 systemd 部署。

## 测试范围

默认测试使用本地 TCP/HTTP/ICMP 回环。历史公网探测测试需要显式设置 `KOMARI_EXTERNAL_PING_TESTS=1`，并具备到对应地址的 IPv4/IPv6 连通性；ICMP 需要 CAP_NET_RAW。
