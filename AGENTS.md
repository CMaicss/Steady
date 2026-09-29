# Steady 开发要求

本文件是仓库的协作约定，适用于人工开发和代码代理。修改前先阅读相关代码与测试，只处理本次需求，不顺带重构无关功能。

## 项目与结构

- Steady 是离线、单用户的 GNOME 原生任务管理应用，使用 Python ≥ 3.10、PyGObject、GTK ≥ 4.12 和 libadwaita ≥ 1.5。
- 应用名为 `Steady`，Python 包和命令为 `steady`，应用标识为 `io.github.steady.Steady`；界面与用户文档使用中文。
- `steady/storage.py` 管理 SQLite、业务校验、备份和恢复；数据约束放在这里，不只依赖界面校验。
- `steady/window.ui` 定义主窗口，`steady/window.py` 负责交互，`steady/dialogs.py` 与 `steady/widgets.py` 提供对话框和公共控件。
- `steady/application.py` 管理生命周期，`steady/timeutils.py` 处理时间，`tools/install.py` 负责用户级安装与旧版迁移。
- `data/` 与 `packaging/` 存放桌面入口、D-Bus、AppStream 和 Flatpak 配置；`tests/` 与 `tools/check_ui.py` 分别验证逻辑和原生界面。

## 产品约束

- 截止时间和 Todo 清单完全可选；任务只填写标题即可创建并正常推进。
- 闭环与 Todo 完成状态独立：全部勾选不自动闭环，闭环不自动勾选；修改已闭环任务前需重新打开。
- 活动记录可编辑、删除，但不回滚任务或 Todo 当前状态；编辑保留原发生时间并记录编辑时间。
- 时间以 UTC 保存、按系统时区展示；没有截止时间不显示倒计时，已闭环任务的到期状态以闭环时刻计算。
- 任务状态与关键词联合筛选；筛选在数据库分页前生效，切换筛选不得丢失未提交草稿。
- 删除需明确确认。失败时保留用户输入，并提供可理解的错误提示，不能静默丢失数据。

## 实现原则

- 优先复用现有实现、Python 标准库和 GTK/libadwaita 原生控件；没有明确需求不增加依赖、抽象层或后台服务。
- 遵循现有代码风格：四空格缩进，函数和变量使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_SNAKE_CASE`。
- 控制改动范围，不做无关格式化或全局改名；公共行为改动先检查所有调用方。
- 保持 GTK 主线程响应，不在启动阶段引入联网、长时间阻塞或不必要的导入。需要时延迟加载对话框。
- 界面跟随系统主题，兼顾窄窗口、键盘操作和可访问性；状态不能只靠颜色表达。
- 不引入云同步、账号、遥测或后台常驻，除非用户明确要求。

## 数据与兼容性

- 默认数据位于 `${XDG_DATA_HOME:-~/.local/share}/steady/`。测试、截图和性能测量只使用隔离临时目录，不读写用户的真实任务来构造测试。
- 数据操作使用事务和参数化 SQL；任务、活动与 Todo 的关联依赖外键，失败时回滚，不留下部分成功的数据。
- 数据库结构变更必须有版本迁移、升级前备份和回归测试；不能删除旧库来“解决”迁移问题。
- 备份导入必须校验格式、关联和大小，恢复前保留安全备份；继续支持旧 `mtodo-backup` 格式。
- 旧名称只保留在迁移、兼容性校验及相应测试中。迁移前要求旧应用正常退出；新旧目录冲突时停止，不覆盖或猜测合并。
- 禁止提交真实数据库、草稿、导出备份、日志、密钥、令牌或含个人内容的截图；`docs/steady.png` 只使用虚构示例数据。

## 验证与交付

在仓库根目录运行：

```bash
/usr/bin/python3 -m unittest discover -s tests -v
/usr/bin/python3 tools/check_ui.py --screenshots .artifacts
git diff --check
```

- 逻辑改动补充对应回归测试；界面改动运行原生检查，并查看宽窄窗口、明暗主题截图。
- 原生检查需要可用的桌面会话。环境不满足时明确说明未运行的检查，不把跳过写成通过。
- 声称启动变快前使用 `tools/benchmark_startup.py` 做相同条件对比，区分缓存、系统负载和代码变化。
- 不在验证过程中自动安装到用户目录或关闭用户应用；此类操作需有本次任务的明确授权。
- README 保持“标题、截图、概述、构建和运行”的简洁结构，详细开发要求写在本文件。
- 发布时同步 `steady/__init__.py`、`meson.build` 和 AppStream 中的版本信息。未实际构建的 Flatpak 不得标记为已验证。

## 提交规范

采用 Google 的变更描述原则与 Angular 风格的提交格式；`type(scope)` 是本仓库约定，不声称 Google 的所有项目都强制此格式。

```text
<type>(<scope>): <summary>

<body>

<footer>
```

- `scope` 可省略；有范围时使用真实模块名，如 `storage`、`ui`、`install`、`docs`、`packaging`，不凭空扩展分类。
- `type` 使用 `feat`、`fix`、`docs`、`refactor`、`perf`、`test`、`build`、`ci` 或 `revert`。
- 标题使用英文、祈使语气和具体动词，摘要以小写开头、末尾无句号；完整标题不超过 72 个字符。
- 标题后空一行；非文档提交必须提供至少 20 个字符的正文，说明做了什么、为什么，以及必要的兼容性影响和验证结果。文档提交可省略正文。
- 有不兼容变更时在页脚说明 `BREAKING CHANGE:`；仅在确有对应议题时使用 `Fixes #123` 等引用，不编造编号。
- 每个提交只处理一个可解释的逻辑单元，避免 `update`、`fix bug`、`wip` 等无信息描述。
- 提交前检查 `git status`、暂存文件和 `git diff --cached --check`，确认没有个人数据、生成缓存或无关文件。
- 仅在用户要求时提交或推送；不强制推送、不重写已发布历史，也不顺带创建分支。保留用户的作者身份，不修改全局 Git 配置。

示例：`fix(storage): preserve drafts during data migration`。

规范依据：
- Google Engineering Practices：`https://google.github.io/eng-practices/review/developer/cl-descriptions.html`
- Angular Commit Message Guidelines：`https://github.com/angular/angular/blob/main/contributing-docs/commit-message-guidelines.md`
