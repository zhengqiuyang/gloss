"""Rich terminal rendering for cards, lists, search results, digests and stats."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import Card

console = Console()

# 终端编码不支持某个符号时（典型：GBK 控制台遇到 emoji），自动降级为 ASCII
# 替身。探测结果按 (字符, 编码名) 缓存；GUI/HTML 里的符号不走这里（WebView 是 UTF-8）。
_glyph_cache: dict[tuple[str, str], bool] = {}

_TYPE_BADGE = {"term": ("📖", "[T]"), "snippet": ("📝", "[S]")}


def _stream_encoding() -> str:
    stream = getattr(console, "file", None) or sys.stdout
    enc = getattr(stream, "encoding", None)
    if not enc:
        enc = getattr(sys.stdout, "encoding", None)
    return enc or "utf-8"


def _encodable(ch: str, enc: str) -> bool:
    key = (ch, enc)
    if key not in _glyph_cache:
        try:
            ch.encode(enc)
            _glyph_cache[key] = True
        except (UnicodeEncodeError, LookupError):
            _glyph_cache[key] = False
    return _glyph_cache[key]


def glyph(emoji: str, fallback: str = "") -> str:
    """终端编码能表示 emoji 时原样返回，否则返回 ASCII 替身。"""
    return emoji if _encodable(emoji, _stream_encoding()) else fallback


def _type_badge(card_type: str) -> str:
    pair = _TYPE_BADGE.get(card_type, _TYPE_BADGE["term"])
    return glyph(pair[0], pair[1])


def _date(iso: str) -> str:
    return iso[:10] if iso else ""


def render_card(
    card: Card,
    cache_hit: bool,
    linked: list[Card] | None = None,
    pending: list[str] | None = None,
    backfilled: list[Card] | None = None,
) -> None:
    linked = linked or []
    pending = pending or []
    backfilled = backfilled or []

    if cache_hit:
        badge = Text(
            f"{glyph('⚡', '*')} cache hit · 0 tokens · 第 {card.hit_count} 次查询",
            style="green",
        )
    else:
        badge = Text(
            f"{glyph('✨', '+')} 已收录新卡片 · {card.tokens_used} tokens", style="cyan"
        )

    body = Text()
    if card.aliases:
        body.append(" aka ", style="dim")
        body.append(" / ".join(card.aliases) + "\n", style="italic")
    if card.one_liner:
        body.append(card.one_liner + "\n\n", style="bold")
    if card.detail:
        body.append(card.detail + "\n")

    if card.tags:
        body.append("\n")
        body.append(" ".join(f"#{t}" for t in card.tags) + "\n", style="magenta")

    all_linked = {c.id: c for c in linked + backfilled if c.id != card.id}
    if all_linked or pending:
        body.append("\n")
        for other in all_linked.values():
            body.append(f"  {glyph('🔗', '->')} {other.term}", style="cyan")
            body.append("  (已关联)\n", style="dim")
        for name in pending:
            body.append(f"  · {name}", style="yellow")
            body.append("  (未收录，gloss 它即可建立关联)\n", style="dim")

    footer_lines = [f"首次记录 {_date(card.created_at)}"]
    if card.source:
        footer_lines.append(f"来源 {card.source}")
    if card.context:
        footer_lines.append(f"语境 {card.context}")
    footer = " · ".join(footer_lines)

    if backfilled:
        names = ", ".join(c.term for c in backfilled)
        footer += f" · {glyph('🔗', '->')} 因新词回填关联: {names}"

    title = f"{_type_badge(card.type)} {card.term}"
    console.print(Panel(body, title=title, subtitle=badge, subtitle_align="left"))
    console.print(Text(footer, style="dim"))


def render_digest(card: Card, key_terms: list[str]) -> None:
    render_card(card, cache_hit=False)
    if key_terms:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_row(Text("值得单独收录的词（gloss <词> 逐个收入）:", style="dim"))
        for i, term in enumerate(key_terms, 1):
            table.add_row(Text(f"  {i:2d}. {term}", style="yellow"))
        console.print(table)


def render_translation(result: dict) -> None:
    if result["cache_hit"]:
        badge = Text(
            f"{glyph('⚡', '*')} 缓存命中 · 0 token · 第 {result['hits']} 次翻译",
            style="green",
        )
    else:
        badge = Text(
            f"{glyph('🌐', '')} 已翻译 · {result['tokens']} tokens · 已缓存",
            style="cyan",
        )
    body = Text()
    body.append(result["source"] + "\n\n", style="dim")
    body.append(result["translation"])
    console.print(Panel(body, title=f"{glyph('🌐', '')} 译文（{result['target']}）",
                        subtitle=badge, subtitle_align="left"))


def render_list(cards: list[Card]) -> None:
    if not cards:
        console.print("[dim]词库还是空的。离线体验: gloss demo · 配置模型后查词: gloss setup[/dim]")
        return
    table = Table(title=f"词库 · {len(cards)} 条")
    table.add_column("词条", style="cyan", no_wrap=True)
    table.add_column("一句话", max_width=48)
    table.add_column("#标签", style="magenta", max_width=24)
    table.add_column("hits", justify="right")
    table.add_column("收录日期", style="dim")
    for c in cards:
        table.add_row(
            f"{_type_badge(c.type)} {c.term}",
            c.one_liner,
            " ".join(f"#{t}" for t in c.tags[:3]),
            str(c.hit_count),
            _date(c.created_at),
        )
    console.print(table)


def render_search(results: list[Card], query: str) -> None:
    if not results:
        console.print(f"[dim]没有匹配 {query!r} 的卡片。[/dim]")
        return
    console.print(f"[bold]搜索 {query!r}[/bold] · {len(results)} 条结果")
    table = Table(show_header=False, box=None, padding=(0, 1))
    for c in results:
        table.add_row(
            Text(f"{glyph('📖', '[T]')} {c.term}", style="cyan"),
            Text(c.one_liner, style="dim"),
        )
    console.print(table)


def render_related(center: Card, linked: list[Card], pending: list[str]) -> None:
    console.print(f"[bold cyan]{center.term}[/bold cyan] 的关联概念")
    if linked:
        for c in linked:
            console.print(f"  {glyph('🔗', '->')} {c.term} [dim]— {c.one_liner}[/dim]")
    for name in pending:
        console.print(f"  · {name} [yellow](未收录)[/yellow]")
    if not linked and not pending:
        console.print("[dim]暂无关联概念。[/dim]")


def render_review(days: int, fresh: list[Card], top: list[Card]) -> None:
    console.print(f"[bold]{glyph('📖', '')} 近 {days} 天复盘[/bold]")
    if not fresh:
        console.print("[dim]这段时间没有新卡片。丢一个词进来: gloss <术语>[/dim]")
    else:
        by_tag: dict = {}
        for c in fresh:
            key = c.tags[0] if c.tags else "未分类"
            by_tag.setdefault(key, []).append(c)
        for tag, cards in sorted(by_tag.items()):
            console.print(f"\n[magenta]#{tag}[/magenta]")
            for c in cards:
                console.print(
                    f"  {glyph('📖', '[T]')} {c.term} [dim]— {c.one_liner}[/dim]"
                )
    if top:
        console.print(f"\n[bold]{glyph('🔥', 'TOP')} 查得最多的词[/bold]")
        for c in top:
            console.print(f"  {c.term} [dim]× {c.hit_count}[/dim]")


def render_stats(stats: dict, top: list[Card]) -> None:
    table = Table(title="词库统计", show_header=False, box=None, padding=(0, 2))
    by_type = stats['by_type']
    table.add_row(
        "卡片总数",
        f"{stats['total']}  (术语 {by_type.get('term', 0)} / 摘要 {by_type.get('snippet', 0)})",
    )
    table.add_row("累计缓存命中", f"{stats['cache_hits']} 次（每次命中都是零 token 的重复查询）")
    table.add_row("累计模型消耗", f"{stats['tokens_spent']} tokens")
    console.print(table)
    if top:
        console.print(
            f"[bold]{glyph('🔥', 'TOP')} 最常查:[/bold] "
            + ", ".join(f"{c.term} ×{c.hit_count}" for c in top)
        )
