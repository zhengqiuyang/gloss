"""Click CLI. Unknown commands fall through to term lookup, so `gloss RAG` just works."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

import click

from . import __version__, db, ingest, ui
from .config import (
    config_path,
    load_config,
    mask_config,
    set_config_value,
    gloss_home,
)
from .export import export_json, export_markdown
from .models import GlossError


class FallbackGroup(click.Group):
    """Route anything that isn't a subcommand to `lookup` (or piped stdin handling)."""

    def parse_args(self, ctx: click.Context, args: List[str]) -> List[str]:
        if not args and not sys.stdin.isatty():
            # `echo QUIC | gloss` arrives as an empty invocation; rewrite it as
            # `gloss pipe <text>` so the normal machinery routes it.
            args = ["pipe", sys.stdin.read()]
        return super().parse_args(ctx, args)

    def resolve_command(self, ctx: click.Context, args: List[str]) -> Tuple[str, Any, List[str]]:
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            return "lookup", self.get_command(ctx, "lookup"), args


@click.command(
    cls=FallbackGroup,
    context_settings={"help_option_names": ["-h", "--help"], "ignore_unknown_options": True},
)
@click.version_option(__version__, prog_name="gloss")
def cli() -> None:
    """gloss — learn a term once, own it forever.

    \b
      gloss RAG                    查询/收录一个术语
      gloss ls                     浏览词库
      gloss search "向量"          全文搜索
      gloss review                 近 7 天复盘
      echo "一段文字" | gloss      把一段话消化成摘要卡片
      gloss img screenshot.png     解析一张图片
    """


@cli.command()
@click.argument("term", nargs=-1, required=True)
@click.option("--refresh", is_flag=True, help="忽略缓存，重新生成这张卡片")
@click.option("-n", "--note", default="", help="记录遇到的语境，如“在读 xx 文档时”")
@click.option("-s", "--source", default="", help="来源，如 URL 或书名")
def lookup(term: Tuple[str, ...], refresh: bool, note: str, source: str) -> None:
    """查询一个术语；本地没有就调用模型生成卡片并收录。"""
    ingest.lookup_or_ingest(" ".join(term), note=note, source=source, refresh=refresh)


@cli.command(hidden=True)
@click.argument("text", nargs=-1)
def pipe(text: Tuple[str, ...]) -> None:
    content = " ".join(text) or _read_stdin()
    ingest.ingest_piped(content)


@cli.command("img")
@click.argument("image", type=click.Path(exists=True))
@click.option("-n", "--note", default="", help="补充说明，如想重点了解图中哪部分")
def img(image: str, note: str) -> None:
    """解析一张图片（截图/图表），生成摘要卡片。"""
    ingest.ingest_image(image, note=note)


@cli.command()
def paste() -> None:
    """直接读剪贴板：文字 → 词条/摘要，截图 → 视觉解析（需配置视觉模型）。

    配合 alias 使用最顺手：alias gp='gloss paste'
    """
    from .clipboard import image_supported_hint, read_clipboard

    kind, data = read_clipboard()
    if kind == "image":
        if not load_config()["llm"].get("vision_model"):
            ui.console.print(f"[yellow]提示: {image_supported_hint()}[/yellow]")
        ingest.ingest_image(data, note="")
    else:
        ingest.ingest_piped(data)


@cli.command()
@click.option("--clear", is_flag=True, help="删除之前 demo 导入的示例卡片")
def demo(clear: bool) -> None:
    """导入一套示例词库（不需要 API key），体验浏览/搜索/关联。"""
    from .demo import clear_demo, seed_demo

    if clear:
        removed = clear_demo()
        click.echo(f"已删除 {removed} 张示例卡片。")
        return
    added, skipped = seed_demo()
    if added:
        click.echo(f"已导入 {added} 张示例卡片" + (f"（跳过 {skipped} 张你已有的）" if skipped else "") + "。试试：\n")
        click.echo("  gloss ls              # 看看词库长什么样")
        click.echo("  gloss RAG             # 打开一张卡片")
        click.echo("  gloss related RAG     # 看概念之间怎么连成网")
        click.echo("  gloss search 向量      # 全文搜索")
        click.echo("  gloss review --days 30 # 复盘")
        click.echo("  gloss stats           # 统计")
        click.echo("\n不想要这些示例了: gloss demo --clear")
    else:
        click.echo("示例卡片之前已经导入过" + (f"（跳过 {skipped} 张重名）" if skipped else "") + "，直接 gloss ls 看。")


@cli.command()
@click.option("-t", "--type", "card_type", type=click.Choice(["term", "snippet"]), default=None)
@click.option("--tag", default=None, help="按标签过滤")
@click.option("--limit", default=50, show_default=True)
def ls(card_type: Optional[str], tag: Optional[str], limit: int) -> None:
    """浏览词库。"""
    ui.render_list(db.list_cards(card_type=card_type, tag=tag, limit=limit))


@cli.command()
@click.argument("name", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="输出原始 JSON（供脚本使用）")
def show(name: Tuple[str, ...], as_json: bool) -> None:
    """只看本地卡片，不触发模型。"""
    import json as jsonlib

    card = db.get_card(" ".join(name))
    if card is None:
        raise GlossError("本地没有这张卡片。去掉 show 直接 gloss <词> 可以联网收录。")
    if as_json:
        data = card.to_row()
        data["links"] = [c.term for c in db.neighbors(card.id)]
        click.echo(jsonlib.dumps(data, ensure_ascii=False, indent=2))
    else:
        ui.render_card(card, cache_hit=False, linked=db.neighbors(card.id),
                       pending=card.related_names)


@cli.command()
@click.argument("query", nargs=-1, required=True)
@click.option("--limit", default=20, show_default=True)
def search(query: Tuple[str, ...], limit: int) -> None:
    """全文搜索词库（FTS5，找不到时回退 LIKE）。"""
    ui.render_search(db.search(" ".join(query), limit=limit), " ".join(query))


@cli.command()
@click.argument("name", nargs=-1, required=True)
def related(name: Tuple[str, ...]) -> None:
    """查看一张卡片关联了哪些概念。"""
    card = db.get_card(" ".join(name))
    if card is None:
        raise GlossError("本地没有这张卡片。")
    ui.render_related(card, db.neighbors(card.id), card.related_names)


@cli.command()
@click.option("--days", default=7, show_default=True, help="复盘最近几天")
def review(days: int) -> None:
    """近 N 天收录复盘 + 最常查的词。"""
    ui.render_review(days, db.created_since(days), db.top_hits())


@cli.command()
def stats() -> None:
    """词库统计。"""
    ui.render_stats(db.counts(), db.top_hits())


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["md", "json"]), default="md", show_default=True)
@click.option("--out", type=click.Path(), default=None, help="输出目录/文件，默认 ./gloss-export")
def export(fmt: str, out: Optional[str]) -> None:
    """导出词库（md = Obsidian 兼容，json = 完整备份）。"""
    if fmt == "json":
        path = export_json(Path(out or "gloss-export/gloss-backup.json"))
        click.echo(f"已导出 {path}")
    else:
        files = export_markdown(Path(out or "gloss-export"))
        click.echo(f"已导出 {len(files)} 个 markdown 文件到 {files[0].parent}/")


@cli.group()
def config() -> None:
    """查看或修改配置。"""


@config.command("show")
def config_show() -> None:
    cfg = load_config()
    masked = mask_config(cfg)
    click.echo(f"配置文件: {config_path()}")
    click.echo(f"数据目录: {gloss_home()}")
    for section, values in masked.items():
        for key, value in values.items():
            click.echo(f"  {section}.{key} = {value!r}")


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """设置配置项，如 gloss config set llm.model gpt-4o-mini"""
    set_config_value(key, value)
    click.echo(f"已写入 {key} = {value!r}")


@config.command("path")
def config_path_cmd() -> None:
    click.echo(config_path())


def _read_stdin() -> str:
    return sys.stdin.read()


def main() -> None:
    """Console-script entry point."""
    try:
        cli()
    except GlossError as exc:
        ui.console.print(f"[red]✗ {exc}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
