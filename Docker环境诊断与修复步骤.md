# Docker 环境诊断与最小修复步骤

日期：2026-10-05。仅检查开发环境；未修改任何项目源码，也未安装软件、启用系统功能或重启计算机。

## 诊断结论

1. **WSL 未安装／不可用，已由系统明确报告。** wsl.exe 是系统入口文件，存在这个文件不代表 WSL2 已安装。
2. **未找到 Docker Desktop 安装。** 当前 PATH 中无 docker；检查当前用户及机器的常见卸载记录、标准安装路径、开始菜单 Docker 快捷方式及 Docker 进程／服务，没有找到安装证据。不能排除未登记的自定义路径，但目前不存在可用入口。
3. **不是仅 PATH 配置缺失。** 即使添加 Docker 标准目录，该目录中的可执行文件当前也不存在，且 WSL 后端仍缺失。
4. **不能在本会话完成系统修复。** Windows 可选功能查询需要提升权限；当前会话没有可用管理员执行通道。WSL 功能安装还可能要求用户重启计算机。没有把查询失败称为安装失败，因为本轮并未执行安装。

## 实际检查与真实错误

| 项目 | 返回／结果 |
|---|---|
| Python platform.platform() | Windows-11-10.0.26200-SP0，AMD64。 |
| Windows CurrentVersion 记录 | DisplayVersion=25H2，CurrentBuild=26200，UBR=9445；ProductName 仍显示 Windows 10 Home China，与运行时名称存在历史命名差异。 |
| wsl --version | 退出码 1；“未安装适用于 Linux 的 Windows 子系统。可通过运行 ‘wsl.exe --install’ 进行安装。” |
| wsl --status | 退出码 50；同上。 |
| wsl --list --verbose | 退出码 1；同上。 |
| docker version | 无法启动进程：[WinError 2] 系统找不到指定的文件。未取得 Client／Server 信息。 |
| docker compose version | 无法启动进程：[WinError 2] 系统找不到指定的文件。 |
| docker info | 无法启动进程：[WinError 2] 系统找不到指定的文件。 |
| Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux | “请求的操作需要提升。” 无法判定可选功能状态。 |
| Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform | “请求的操作需要提升。” 无法判定可选功能状态。 |
| Get-CimInstance Win32_OperatingSystem／Win32_Processor | “无法从客户端中访问 CIM 资源。” 未取得完整 CPU／虚拟化信息。 |
| Windows IsProcessorFeaturePresent(21) | true；此执行环境可见的固件虚拟化标记为开启。 |
| Windows IsProcessorFeaturePresent(20) | false；当前可见 SLAT 标记未报告支持。与受限环境／CPU 实际能力的关系尚未核实，不能据此声称满足 Docker 所有硬件要求。 |

检查过的 Docker 文件路径：

- C:/Program Files/Docker/Docker/resources/bin/docker.exe
- C:/Program Files/Docker/Docker/Docker Desktop.exe
- C:/Users/gia/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe

这些路径当前均不存在。PATH 未修改，未扫描用户凭据或项目外无关资料。

## 最小可行修复路径

### 1. 在 Windows 管理员 PowerShell 安装 WSL

```powershell
wsl --install --no-distribution
```

按安装提示重启计算机；重启后执行：

```powershell
wsl --update
wsl --set-default-version 2
wsl --version
```

Docker 当前要求 WSL 2.1.5 或更高版本。--no-distribution 避免额外安装 Ubuntu；Docker Desktop 使用自己的 Linux 环境，不要求另装通用发行版。

如果安装报虚拟化错误，需要在宿主 Windows／固件界面核实硬件虚拟化及 SLAT 支持；当前受限会话的 CPU 查询不足以确认全部先决条件。只有报错或核实不满足时才进入该分支，不能把安装前检查当作运行验证。

### 2. 安装并启动 Docker Desktop

从官方 Windows 安装页下载 x86_64 安装程序。可用时选择当前用户安装，后端使用 WSL2、Linux containers；安装后启动 Docker Desktop，完成首次启动要求并等待 Engine 就绪。Docker Desktop 已包含 Docker CLI 和 Compose，不另装独立 docker-compose。

安装完成后重新打开终端及 Codex，以获取新的 PATH。仅在 docker.exe 实际存在且完整路径调用成功、普通 docker 调用失败时，才判断为 PATH 问题：将实际安装目录的 resources/bin 加入用户 PATH 后重新启动终端；不盲目添加不存在的目录或覆盖已有 PATH。

### 3. 在当前客户端重新实际验证

```powershell
docker version
docker compose version
docker info
```

通过条件：三条命令均成功退出；docker version 有 Client 和 Server；docker compose version 返回 Compose 版本；docker info 能连接 Engine，OSType 为 linux。只显示 Client 或启动失败不算通过。

## 当前状态

三条指定 Docker 命令已实际尝试，均因 docker 可执行文件不可用而失败。环境没有完成修复，因此没有“修复后通过”的结果；仍受 WSL／Docker 安装及管理员系统操作条件阻塞。环境可用后仅复验上述命令并停止，不继续业务开发。

### 后续用户截图记录

用户在管理员 Windows PowerShell 实际执行 `wsl --install --no-distribution`，截图返回“已禁止(403)。”。这是用户环境中的真实安装错误，不是本 Agent 执行结果。仅凭该信息不能确定拒绝来自具体下载端点、代理还是访问策略，也不能据此认定管理员权限不足。

下一步先在同一管理员窗口尝试 `wsl --install --no-distribution --web-download`，使用官方提供的另一下载路径。此命令尚未执行，不能记录为成功。如果仍返回 403，可按 Microsoft 官方离线安装说明，从 microsoft/WSL 的正式 Release 下载适合本机 AMD64 的 x64 MSI 包，并启用 VirtualMachinePlatform 后按提示重启；不要下载仅内核更新包或非官方来源软件。

## 官方依据

- [Windows 安装 Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
- [Docker Desktop WSL2 后端：无需另装通用发行版](https://docs.docker.com/desktop/features/wsl/)
- [Microsoft WSL 基本命令：--no-distribution](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)
- [Docker Compose 安装：Docker Desktop 包含 Compose](https://docs.docker.com/compose/install/)
