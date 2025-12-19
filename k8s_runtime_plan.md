# K8s Runtime Support Plan for OpenHands

目标：让 OpenHands 在 Kubernetes 集群中运行评测任务（例如 SWE-bench），用 Pod/Job 替代本地 Docker runtime，同时支持本地源码挂载以避免频繁重建镜像。

## 0. 前置约束与假设
- 集群：已有可用的 K8s 集群（如 ACK/AKS/EKS），可访问镜像仓库（如 ACR），具备创建 Pod/Job 的权限。
- 镜像策略：不在集群内 build；runtime 镜像预先构建并推送到仓库，或使用已有固定镜像。
- 存储：可用 PVC/emptyDir/hostPath（取决于环境安全策略）；可选共享缓存卷。
- 身份与安全：有合适的 ServiceAccount、RBAC；允许 imagePullSecrets；非特权容器优先。

## 1. 设计概览
- 新增 Kubernetes Runtime/Sandbox，实现与现有 Runtime 接口兼容：
  - Pod/Job 创建、等待 ready、流式日志、命令执行、文件读写/传输。
  - 支持单实例一个 Pod（默认），可选 Job 控制器。
- 配置项（OpenHandsConfig 扩展）：
  - runtime: `k8s`
  - `kubernetes.*`：namespace、serviceAccount、imagePullSecrets、nodeSelector/tolerations/affinity、volumes/volumeMounts、storageClass/PVC 名、hostPath/emptyDir 选项、cleanupPolicy（总保留/失败保留/总清理）、pullPolicy。
  - runtime 镜像：`runtime_image_repo` + tag（预构建），或直接显式指定。
- 挂载源码模式（可选，避免频繁重建镜像）：
  - `runtime.mount_source`（bool，默认 false）+ `runtime.source_host_path`（宿主/K8s 可访问路径/PV）。
  - 容器内 PYTHONPATH 指向挂载目录；镜像不再打包源码（仅依赖）。

## 2. 组件与实现要点
1) **KubernetesRuntimeBuilder**（或扩展现有 RuntimeBuilder）：
   - 替代 DockerRuntimeBuilder 的镜像存在性检查，可选跳过 build（固定镜像）。
   - 仅验证镜像可拉取/可用（`ImagePullPolicy` 支持）。
2) **KubernetesSandboxService**（或 runtime 实现）：
   - Pod/Job 模板渲染：容器镜像、命令/entrypoint（运行 agent server）、env、资源 requests/limits、securityContext（非特权）、volumeMounts。
   - 等待 Pod ready：watch Pod 状态，处理拉取失败/调度失败。
   - 命令执行与文件 IO：
     - `kubectl exec`/SPDY 或 API attach 模式，提供流式 stdout/stderr。
     - 文件读写：`kubectl cp` 等价实现或 sidecar/volume 共享路径。
   - 日志与清理：按 cleanupPolicy 清理 Pod；失败保留供调试；可选把日志上传到对象存储/持久卷。
3) **配置加载与切换**：
   - 在 OpenHandsConfig/CLI 增加 `runtime=k8s` 入口；加载 `kubernetes.*` 子配置。
   - 保持 Docker runtime 兼容；默认仍用 Docker。
4) **卷与挂载策略**：
   - Workspace/临时目录：emptyDir（默认）或 PVC（可缓存 pip/apt）。
   - 源码挂载：可选 hostPath/PVC/ConfigMap（视安全策略）；容器内路径统一 `/app/openhands`。
   - Cache/Buildx：可选共享缓存卷（仅在允许的环境）。
5) **安全与网络**：
   - 非特权、禁用特权、只读根文件系统（如可行）、最小 capabilities。
   - 网络策略：按需开启/限制；默认关闭 HostNetwork。
   - RBAC：最小权限（创建/删除 Pod 或 Job，get/log/exec）。
6) **性能与伸缩**：
   - 并发控制：限制同时运行的 Pod 数；队列/重试。
   - 失败重试策略：拉取失败/调度失败/Pod CrashLoop 的处理。

## 3. 具体实施步骤
1) **配置层扩展**：
   - 扩展 OpenHandsConfig（`runtime: k8s`）和 `kubernetes_config`，添加字段：namespace、serviceAccount、imagePullSecrets、nodeSelector、tolerations、volumes、pullPolicy、cleanupPolicy、mount_source 等。
   - CLI/env 支持：`OH_RUNTIME=k8s`，`OH_K8S_*` 环境变量。
2) **Runtime 实现**：
   - 新增 `KubernetesRuntime`（实现 connect/run_action/copy_to/copy_from/close）。
   - 新增 `KubernetesRuntimeBuilder`（检查/获取镜像，可选跳过 build）。
   - Pod 模板组装与创建；等待 ready；失败错误分类。
3) **挂载源码模式**（可选）：
   - builder 中跳过源码打包；tag 计算忽略源码 hash。
   - sandbox 创建时追加 volumeMount 映射宿主/PV 源码路径；入口脚本调整 PYTHONPATH。
4) **测试与验证**：
   - 本地 kind/minikube 冒烟：单实例 SWE-bench 流程能跑通。
   - 失败场景：镜像拉取失败、调度失败、exec 失败、文件传输失败；确保错误上报。
   - 并发场景：多实例并行，观察资源/调度/日志。
   - 回归：Docker runtime 仍可用。
5) **文档与示例**：
   - 提供示例配置（values/env），创建 namespace/SA/imagePullSecrets/PVC 的指南。
   - 说明挂载源码模式与固定镜像模式的取舍。

## 4. 后续可选增强
- 使用 Job 控制器管理生命周期与重试。
- 日志/指标导出到 ELK/Prometheus。
- 镜像签名/策略（OPA/Gatekeeper）。
- 远程缓存/分布式缓存（若允许）。
