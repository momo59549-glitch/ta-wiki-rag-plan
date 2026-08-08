"""Create an idempotent first batch of evidence-backed KnowledgeCard drafts.

The bootstrap deliberately stops at ``draft``. Publication remains an independent
human content-review action handled by :class:`FileKnowledgeRepository`.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .service import EvidenceReference, FileKnowledgeRepository, KnowledgeError


@dataclass(frozen=True, slots=True)
class BookCardSeed:
    title: str
    chapter_title: str
    claim: str
    limitations: tuple[str, ...]


FIRST_CANDLESTICK_SEEDS: tuple[BookCardSeed, ...] = (
    BookCardSeed(
        "锤子线与上吊线",
        "伞形线",
        "两者具有位于价格区间上端的小实体、至少约为实体两倍的长下影线，以及很短或没有的上影线。相同外形必须结合此前趋势解释：下跌后为潜在看涨的锤子线，上涨后为潜在看跌的上吊线；上吊线还需要后续看跌验证。",
        ("形态只表示潜在线索，不保证反转。", "必须等待本周期收盘后识别，并结合趋势、位置、风险报偿和失效价位。"),
    ),
    BookCardSeed(
        "吞没形态（抱线形态）",
        "吞没形态（抱线形态）",
        "吞没形态由颜色相反的相邻实体构成，第二根较长实体覆盖第一根较小实体。下跌趋势后的看涨吞没与上涨趋势后的看跌吞没，分别提示原趋势可能失去主导；覆盖程度、此前趋势及附近支撑阻力决定其意义。",
        ("比较的是实体而不是必须覆盖全部上下影线。", "需结合趋势背景和后续价格行为，不能单独作为交易指令。"),
    ),
    BookCardSeed(
        "乌云盖顶形态",
        "乌云盖顶形态",
        "乌云盖顶是上涨背景中的两蜡烛线看跌反转警告：先出现较强白色实体，随后黑色实体高开后回落，并深入前一白色实体；通常收盘深入超过前一实体中点时信号更典型，刺入越深负面含义越强。",
        ("必须有此前上涨背景。", "未达到典型深入程度时只能降低置信度，不能强行归类。", "后续突破形态高点会削弱或否定看跌解释。"),
    ),
    BookCardSeed(
        "刺透形态",
        "刺透形态",
        "刺透形态是下降背景中的两蜡烛线看涨反转警告：先出现较强黑色实体，随后白色实体低开后反弹，收盘深入前一黑色实体；典型形态要求收盘超过前一实体中点，深入程度越高，看涨含义通常越强。",
        ("必须有此前下降背景。", "若反弹未超过前一实体中点，应降低置信度并与其他信号区分。", "后续跌破形态低点会削弱或否定看涨解释。"),
    ),
    BookCardSeed(
        "启明星形态",
        "启明星形态",
        "启明星是下降趋势后的三蜡烛线底部反转警告：长黑实体之后出现体现犹豫的小实体，第三根为较强白色实体并收回第一根黑色实体的较大部分。中间星线实体颜色不是核心，第三根的恢复力度与形态所处支撑位置更重要。",
        ("股票、期货等市场的跳空表现不同，不应把理想跳空设为所有市场的绝对条件。", "必须结合下降趋势、第三根确认及失效位置。"),
    ),
    BookCardSeed(
        "黄昏星形态",
        "黄昏星形态",
        "黄昏星是上涨趋势后的三蜡烛线顶部反转警告：长白实体之后出现体现犹豫的小实体，第三根为较强黑色实体并回落至第一根白色实体内部。中间星线实体颜色不是核心，第三根下压的深度越明显，形态通常越有意义。",
        ("不同市场的跳空特征不同。", "必须结合上涨趋势、第三根确认、阻力位置和形态高点失效条件。"),
    ),
    BookCardSeed(
        "流星线与倒锤子线",
        "流星形态与倒锤子形态",
        "两者均有位于价格区间下端的小实体和较长上影线。上涨后出现时为潜在看跌的流星线；下降后出现时为潜在看涨的倒锤子线。倒锤子线需要随后开盘或尤其收盘高于其实体的看涨验证。",
        ("相同外形不能脱离此前趋势命名。", "单根流星线通常只是警告；形态高点被有效突破后应重估看跌观点。"),
    ),
    BookCardSeed(
        "孕线与十字孕线",
        "孕线形态",
        "孕线由前一根较长实体包住后一根较小实体构成，要求包含的是实体，后者影线可以超出前者范围；两根实体不必颜色相反。它提示原趋势动能减弱而非保证反向，第二根若为十字线则形成更强的十字孕线警告。",
        ("孕线可能把趋势从上升或下降转为横向，而非必然反转。", "实体大小带有相对性，应使用预先固定的量化口径并结合位置验证。"),
    ),
)


def _chapter_index(manifest: Path) -> dict[str, str]:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("source_type") != "epub" or payload.get("citation_locator") != "chapter_href":
        raise KnowledgeError("需要 chapter_href 定位的 EPUB manifest")
    return {str(item.get("title")): str(item.get("href")) for item in payload.get("chapters", [])}


def bootstrap_first_candlestick_cards(
    manifest: Path,
    knowledge_root: Path,
    *,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Create missing draft cards and return created/skipped titles."""
    chapters = _chapter_index(manifest)
    repository = FileKnowledgeRepository(knowledge_root)
    existing_titles = {card.get("title") for card in repository.list_cards()}
    result: dict[str, list[str]] = {"created": [], "skipped": []}

    for seed in FIRST_CANDLESTICK_SEEDS:
        if seed.title in existing_titles:
            result["skipped"].append(seed.title)
            continue
        locator = chapters.get(seed.chapter_title)
        if not locator:
            raise KnowledgeError(f"EPUB 缺少目标章节: {seed.chapter_title}")
        if not dry_run:
            repository.create_draft(
                title=seed.title,
                claim=seed.claim,
                source_case_id=None,
                evidence_refs=[EvidenceReference("epub_chapter", str(manifest.resolve()), locator)],
                research_artifacts=[],
                limitations=list(seed.limitations),
            )
        result["created"].append(seed.title)
    return result
