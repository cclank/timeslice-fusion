# GitHub 发布检查清单

## 仓库设置检查清单

- [x] 项目名称: timeslice-fusion
- [x] 许可证: MIT License
- [x] README.md 完整（包含徽章、部署说明等）
- [x] .gitignore 配置完成
- [x] 贡献指南: CONTRIBUTING.md
- [x] 问题模板: .github/ISSUE_TEMPLATE.md
- [x] 保护敏感信息（API 密钥、密码等不在仓库中）
- [x] 无大型二进制文件或用户数据在仓库中
- [x] 代码有适当的注释和文档

## 安全检查

- [x] 环境变量文件（.env）已加入 .gitignore
- [x] API 密钥和其他敏感信息不会被提交
- [x] 输出目录（tasks/, outputs/ 等）已加入 .gitignore
- [x] 临时文件已正确忽略

## 部署准备

- [x] 所有必要的运行说明已在 README.md 中
- [x] 依赖项清晰列明（uv 包管理器）
- [x] 启动脚本可用（start.sh）
- [x] 环境配置说明完整

## Git 状态

- [ ] 提交所有更改
- [ ] 设置远程仓库
- [ ] 推送至 GitHub