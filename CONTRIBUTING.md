# 贡献指南

感谢您有兴趣为 TimeSlice Fusion 做出贡献！本指南将帮助您了解如何参与项目开发。

## 行为准则

请遵循我们的行为准则，保持友好、尊重的社区环境。

## 开始贡献

### 环境设置

1. Fork 仓库
2. 克隆您的 fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/timeslice-fusion.git
   ```
3. 进入项目目录:
   ```bash
   cd timeslice-fusion
   ```
4. 安装依赖:
   ```bash
   pip install uv  # 如果尚未安装 uv
   ```
5. 启动开发服务器:
   ```bash
   ./start.sh
   ```

### 分支策略

- `main`: 主分支，稳定版本
- `develop`: 开发分支，集成新功能
- `feature/*`: 新功能分支
- `hotfix/*`: 紧急修复分支

### 提交规范

请使用以下格式提交代码：

```
<type>(<scope>): <subject>
<BLANK LINE>
<body>
<BLANK LINE>
<footer>
```

类型包括：
- feat: 新功能
- fix: Bug 修复
- docs: 文档更新
- style: 代码格式调整
- refactor: 重构
- test: 测试相关
- chore: 构建过程或辅助工具变动

### 代码风格

- Python: 遵循 PEP 8 标准
- JavaScript: 使用合理的缩进和命名规范
- CSS: 使用语义化类名

## 提交 Pull Request

1. 确保您的分支是最新的
2. 提交清晰的 commit 信息
3. 在 PR 描述中解释更改内容和原因
4. 确保测试通过（如果有的话）