# TimeSlice Fusion - 时空融影

[![License](https://img.shields.io/github/license/lank/TimeSlice-Fusion)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.115.0+-green.svg)](https://fastapi.tiangolo.com/)
[![uv](https://img.shields.io/badge/uv-package_manager-purple.svg)](https://docs.astral.sh/uv/)
[![360 Video](https://img.shields.io/badge/video-360°_panorama-orange.svg)](https://en.wikipedia.org/wiki/360-degree_video)
[![AI Powered](https://img.shields.io/badge/AI-powered_by_QwenVL-red.svg)](https://www.aliyun.com/product/dashscope)

将 360° 全景风景视频与自拍照融合为"你在风景中"的电影级短视频。

## 🌟 特性

- ✨ AI 驱动的智能帧选择算法
- 📸 360° 全景视频处理
- 👤 高质量人像抠图与场景融合
- 🎬 电影级视频生成效果
- 🌐 现代化 Web 界面
- 🚀 快速部署与启动

## 项目概述

TimeSlice Fusion 是一个创新的 AI 视频生成工具，能够将 360° 全景风景视频与用户自拍照融合，创造出仿佛置身于风景中的电影级短视频。

核心技术特点：
- 360° 全景视频帧提取
- AI 驱动的帧选择算法
- 人像抠图与场景融合
- I2V（图像到视频）动画生成

## 项目结构

```
timeslice-fusion/
├── server.py          # FastAPI 后端服务器
├── web/               # 前端界面
│   └── timeslice-fusion.html
├── scripts/           # 核心处理脚本
│   ├── timeslice.py   # 主处理逻辑
│   └── remove_bg.swift # macOS 人像抠图
└── start.sh           # 快速启动脚本
```

## 启动服务

### 方法一：使用启动脚本（推荐）

```bash
# 确保已安装 uv
pip install uv --break-system-packages

# 启动服务（默认端口 8000）
./start.sh

# 或指定端口
./start.sh 3000
```

### 方法二：直接运行

```bash
# 安装依赖并启动
uv run server.py
```

### 环境变量

- `PORT` - 服务器端口 (默认: 8000)
- `CLEANUP_AFTER_MINUTES` - 清理过期任务的时间（分钟，默认: 60，设为0禁用自动清理）

## 使用方法

1. 启动服务后，在浏览器中访问 `http://localhost:8000`
2. 上传 360° 全景风景视频和自拍照
3. 选择风格和持续时间
4. 点击生成，等待处理完成
5. 查看并下载生成的视频

## 技术栈

- 后端: FastAPI
- 前端: HTML/CSS/JavaScript
- AI 引擎: Qwen-VL (阿里云 DashScope)
- 处理工具: FFmpeg, Pillow
- 包管理: uv (Python)

## 处理流程

1. 人像预处理（裁剪、抠图）
2. 360° 视频帧提取
3. AI 选帧（选择最佳场景）
4. 场景和人物分析
5. 人物与场景合成
6. 视频生成（I2V 动画化）

## 注意事项

- 项目使用 macOS Vision Framework 进行人像抠图
- 需要网络连接以调用 AI 服务
- 大视频文件处理可能需要较长时间

## 部署到 GitHub

### 准备工作

1. **Fork 仓库**：点击右上角的 Fork 按钮将仓库复制到你的账户
2. **克隆仓库**：
   ```bash
   git clone https://github.com/你的用户名/timeslice-fusion.git
   cd timeslice-fusion
   ```

### 环境配置

#### 本地运行

```bash
# 安装 uv（如果尚未安装）
pip install uv

# 启动服务
./start.sh
```

#### 使用 Docker（推荐用于生产环境）

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim

WORKDIR /app

COPY . .
RUN uv sync --all-extras

EXPOSE 8000
CMD ["uv", "run", "server.py"]
```

### API 密钥配置

> ⚠️ **安全提醒**: 本项目使用阿里云 DashScope API（Qwen-VL），请确保 API 密钥安全

1. 在根目录创建 `.env` 文件：
   ```
   DASHSCOPE_API_KEY=your_api_key_here
   ```
2. 将 `.env` 添加到 `.gitignore` 中（已在默认配置中包含）

### GitHub Actions 配置（可选）

如需自动部署到云服务，可参考以下 `.github/workflows/deploy.yml` 配置：

```yaml
name: Deploy to Cloud

on:
  push:
    branches: [ main ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3

    - name: Setup uv
      uses: astral-sh/setup-uv@v2
      with:
        version: latest

    - name: Install dependencies
      run: uv sync --all-extras

    - name: Run tests
      run: python -m pytest tests/

    - name: Deploy
      env:
        DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}
      run: |
        # 添加部署命令
```

## 安全性说明

- 项目不会存储用户的视频和图片文件，处理完成后会定期清理
- 所有上传的文件仅在本地处理，不会上传到任何第三方服务（除了AI API调用）
- 敏感配置文件（如 `.env`）已在 `.gitignore` 中排除
- 使用 CORS 中间件允许所有来源（仅在开发环境中）- 生产环境请配置具体域名

## 贡献指南

欢迎提交 Issues 和 Pull Requests 来帮助改进项目！

### 开发流程

1. Fork 仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 许可证

本项目采用 [MIT 许可证](LICENSE) - 详见 [LICENSE](LICENSE) 文件

## 支持

如有问题，请提交 Issue 或联系：
- 项目主页: https://github.com/lank/timeslice-fusion
- 文档: 请参阅相关注释和文档