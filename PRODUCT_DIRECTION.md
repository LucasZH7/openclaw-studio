# OpenClaw Dashboard Product Direction

## One-Year Product State

如果一年后这个产品已经成熟，团队规模来到几十人，订阅用户达到数百万，那么它不应该只是一个本地监工页面，而应该是一个真正的 `agent operations platform`。

那个阶段的产品状态应当是：

- 多代理:
  一个工作台同时接入多个 OpenClaw agent，也能接入其他兼容 agent。
- 多端:
  本地桌面版、Web 控制台、移动端轻监控全部打通。
- 角色权限:
  个人、团队负责人、运营、管理员都有不同权限和视图。
- 订阅化:
  免费版看基础状态，专业版看趋势、自动修复、团队报表、历史回放、策略编排。
- 编排能力:
  不只是“监控”，还可以做告警、恢复、调度、协同和审计。
- 市场化:
  用户可以直接在 GitHub 安装，在 ClawHub 订阅，在产品内启用模板与主题。

## Product Pillars

### 1. Lovable Surface
- 一眼能看懂 agent 在干嘛。
- 视觉形象有记忆点，有人格，但不过度卡通。
- 默认体验可爱、稳定、低噪音。

### 2. Operational Trust
- 状态判断可靠，不乱报，不抽风。
- 趋势、token、效率、会话新鲜度等关键指标统一。
- 每一次自动修复都可解释、可追溯、可关闭。

### 3. Team Readiness
- 支持多个 agent、多个 workspace、多个 owner。
- 能看到谁在工作、谁卡住、谁掉线、谁在等待外部回复。
- 支持日报、周报、告警路由与负责人归属。

### 4. Distribution
- GitHub 上开源核心壳层与基础主题。
- ClawHub 上架品牌版、团队版、行业模板版。
- 插件化扩展头像、主题、自动化策略与数据源。

## What This Project Needs To Become That

### Phase A: Productize The Single-Agent Core
- 把当前本地监控台打磨到稳定、可安装、可演示。
- 完成统一设置、稳定动画、品牌文案、版本管理。
- 保证默认体验“开箱就好看”。

### Phase B: Publish-Ready
- 整理 GitHub 仓库结构。
- 补 README、安装脚本、截图、发布说明。
- 做一个面向 ClawHub 的 listing bundle。

### Phase C: Team Features
- 支持多个 agent 卡片和总览页。
- 增加团队健康分、工时分布、token 成本趋势。
- 增加团队角色和项目维度筛选。

### Phase D: Revenue Features
- 订阅套餐、主题包、头像包、行业模板。
- 高级自动化、自定义规则、回放审计。
- 团队协作和云同步。

## Current Push Direction

当前这一版最重要的推进方向，不是继续堆零散效果，而是：

1. 让默认视觉和交互稳定、可爱、低噪音。
2. 让 README 和文案开始像一个产品，而不是脚本集合。
3. 为 GitHub 发布和 ClawHub 上架预留清晰结构。

## Shipping Criteria For The Next Milestone

下一里程碑至少满足这些条件：

- 本地安装三步内完成。
- 默认页面视觉稳定，不出现明显抽风动画。
- 所有主要设置都能持久化。
- 有一个清晰的产品名、简介、截图与版本说明。
- 可以打包成一个可公开演示的仓库。
