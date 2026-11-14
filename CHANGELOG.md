# Changelog

All notable changes to this project will be documented in this file.

格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/)。

## [Unreleased]

### Fixed
- Windows 中文控制台（GBK/cp936）下 CLI 崩溃：终端输出按编码能力自动降级
  emoji 为 ASCII 替身（UTF-8 终端显示不变）；`gloss ls` 等命令不再
  `UnicodeEncodeError`（#1 中的 G-P0-1）

### Changed
- 版本号单一来源：`pyproject.toml` 改为 dynamic version，读取
  `src/gloss/__init__.py`（G-P0-4）

### Added
- 测试与 CI：Windows leg（windows-latest + py3.12）跑全量 pytest（G-P0-3）；
  控制台编码回归测试（G-P0-1）；ruff + mypy + coverage 门禁（G-P1-1，
  覆盖率门槛 72% 向上棘轮）；构建通道验证 `uv build` + wheel 冒烟（G-P1-2）
- 发布通道：tag `v*` 触发 release workflow，经 PyPI Trusted Publisher 发布
  （首发前需人工配置 Trusted Publisher，见 release.yml 注释）
