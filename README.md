# 旅游路线定制系统

一个基于 Flask + Vue2 + Element UI 的行程规划工具，支持多天行程、活动/交通/地址管理，以及时间轴可视化展示。

## 功能概览

- 创建与删除行程（开始/结束日期与时间）
- 新增、编辑、删除条目：
  - 活动
  - 交通
  - 地址
- 支持跨天条目（开始与结束可选完整日期时间）
- 时间轴展示（日夜背景、日期分段、刻度、条目标注）
- 地址可选高德自动地理解析（经纬度）
- 本地化前端依赖（无 CDN 也可运行）

## 技术栈

- 后端：Flask（Python 3.11）
- 前端：Vue 2 + Element UI（本地静态资源）
- 数据库：SQLite

## 目录结构

```text
.
├── app.py
├── requirements.txt
├── templates/
│   └── index.html
├── static/
│   ├── style.css
│   ├── js/
│   │   ├── api.js
│   │   ├── app.js
│   │   └── timeline.js
│   └── vendor/
│       ├── vue/
│       └── element-ui/
└── travel_plan.db
```

## 本地运行

### 1) 创建并激活虚拟环境（可选）

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 2) 安装依赖

```bash
pip3.11 install -r requirements.txt
```

### 3) 启动服务

```bash
python3.11 app.py
```

启动后访问：

```text
http://127.0.0.1:5000
```

## 配置说明

- 高德 Key 在页面左下角“设置”中填写。
- 若不填写高德 Key，手动输入经纬度也可使用地址功能。

## 数据说明

- 默认数据库文件：`travel_plan.db`
- 该文件通常不建议提交到 Git（已可通过 `.gitignore` 排除）。
