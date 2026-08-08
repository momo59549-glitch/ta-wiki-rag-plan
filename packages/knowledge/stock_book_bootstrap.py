"""Curated, page-cited stock-analysis rules from reviewed PDF books."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .service import EvidenceReference, FileKnowledgeRepository, KnowledgeError


@dataclass(frozen=True, slots=True)
class PdfRuleSeed:
    title: str
    source: str
    pages: tuple[int, ...]
    claim: str
    limitations: tuple[str, ...]


PDF_STOCK_RULE_SEEDS: tuple[PdfRuleSeed, ...] = (
    PdfRuleSeed("趋势的高低点结构定义", "schwager", (43,), "上升趋势应表现为相继抬高的相对高点和相对低点；下降趋势则相反。趋势标签必须由已经完成的摆动点确认，不能使用尚未完成的未来高低点。", ("摆动点窗口必须预先固定。", "该定义描述结构，不单独构成买卖指令。")),
    PdfRuleSeed("趋势线的连接与失效", "schwager", (43,), "上升趋势线连接一系列抬高的相对低点，下降趋势线连接一系列降低的相对高点。跌破或突破趋势线只表示原结构受到挑战，应结合收盘确认和后续价格行为判断失效。", ("趋势线选点存在主观性，量化时必须固定触点、容差和确认口径。",)),
    PdfRuleSeed("盘整区内形态信号降权", "schwager", (61,), "当价格仍处于明确交易区间内部时，缺口、旗形等局部形态的方向含义通常较弱；只有接近区间边界并发生经确认的突破时，才应提升其研究权重。", ("区间宽度、持续期和突破阈值需要预注册。",)),
    PdfRuleSeed("三角形必须分类并等待突破", "schwager", (97,), "三角形至少应区分对称、上升和下降三类。收敛外形本身不是方向确认，研究信号应在价格以预定口径突破边界后产生。", ("不得使用突破后的数据反向调整趋势线。", "不同三角形的统计表现必须在目标市场重新验证。")),
    PdfRuleSeed("旗形与三角旗的持续及失效条件", "schwager", (97, 101), "旗形和三角旗是趋势中的窄幅、短期整理结构；顺原趋势突破才构成持续候选信号，向相反方向显著突破则否定持续解释，并可将形态另一端作为近似风险参考。", ("持续期、窄幅程度和显著突破阈值必须量化。", "风险参考不等于保证成交的止损价。")),
    PdfRuleSeed("双顶双底需要趋势转换证据", "schwager", (106, 107), "双顶或双底不能只凭两个相似峰谷命名；它应出现在既有趋势之后，并具有足够的中间回撤以及对转换结构的确认。浅回撤的相邻峰谷应避免强行归类。", ("峰谷相似度和最小回撤幅度必须固定。",)),
    PdfRuleSeed("头肩形态的完成条件", "schwager", (108, 112), "头肩形态由中间更高或更低的头部及两侧肩部组成，但只有在此前存在显著价格趋势且颈线被确认突破后，才视为完成形态。", ("未突破颈线只能标记候选观察。", "肩部对称性不应事后优化。")),
    PdfRuleSeed("止损应对应结构失效", "schwager", (143, 149), "保护性止损应优先放在能够否定入场逻辑的近期结构点之外；若结构止损距离造成不可接受的资金风险，可以放弃交易或使用预先定义的资金止损，但不能事后扩大风险。", ("需计入跳空、涨跌停和滑点导致的止损失效。", "仓位大小应由止损距离反推。")),
    PdfRuleSeed("形态目标位低于入场信号可信度", "schwager", (153, 154), "测量幅度可以形成情景目标，但图表形态对目标价的可靠性通常低于其作为入场或趋势变化线索的可靠性；目标位不应作为保证收益或唯一退出条件。", ("目标位命中率必须独立统计。",)),
    PdfRuleSeed("拒绝孤立的优选历史案例", "schwager", (263, 264, 265), "单个精心挑选的历史图表无法证明策略有效。候选规则必须跨标的、跨时间和跨市场状态检验，并报告全部样本而不是仅展示成功案例。", ("必须保留失败样本和未成交样本。",)),
    PdfRuleSeed("策略优先采用较少参数", "schwager", (266,), "在性能没有实质恶化时，应优先选择参数更少、结构更简单的策略；增加确认条件或修饰规则会扩大搜索空间，必须计入多重试验风险。", ("不能为了简单而删除有明确经济含义的必要约束。",)),
    PdfRuleSeed("回测期间必须覆盖多种市场状态", "schwager", (267, 268), "测试期过短会遗漏不同市场状态，过长又可能混入已失去代表性的制度环境。应同时报告完整期间和多个子期间表现，以检查时间稳定性。", ("具体年数不是跨市场通用常数。", "市场制度变化需要单独标注。")),
    PdfRuleSeed("回测必须使用可实现成交与完整成本", "schwager", (269, 270), "回测必须计入佣金之外的滑点，并禁止在涨跌停或其他不可成交状态假设理想成交；高换手系统对这些假设尤其敏感。", ("成本和成交规则应冻结在实验协议中。",)),
    PdfRuleSeed("选择参数平台而非单点最优", "schwager", (271,), "参数选择应寻找邻近参数都表现稳健的宽阔平台，而不是历史结果最好的孤立单点；单点尖峰更可能是样本偶然性。", ("平台稳定仍不替代真正的样本外验证。",)),
    PdfRuleSeed("优化结果不得作为最终业绩估计", "schwager", (272,), "历史优化几乎必然高估未来表现。优化只可用于确定合理参数区域，最终评价必须来自未参与选择的样本外或锁箱数据。", ("任何查看锁箱结果后的修改都必须启动新版本和新锁箱。",)),
    PdfRuleSeed("双底须收盘突破确认线", "bulkowski", (237, 238), "双底候选由两个低点构成，但只有价格收盘突破两底之间的最高点确认线后，才视为有效双底；此前只能保存为候选观察。", ("书中统计来自其样本，不可直接移植为 A 股胜率。",)),
    PdfRuleSeed("双顶须收盘跌破确认线", "bulkowski", (300,), "双顶候选只有在价格收盘跌破两顶之间的谷底确认线后才完成；两个相似高点本身不足以确认反转。", ("确认可能明显滞后，应同时评估风险报偿。",)),
    PdfRuleSeed("形态失败率必须按阈值完整报告", "bulkowski", (10, 31), "形态研究不能只报告平均涨跌幅，应同时报告不同收益阈值下的失败率、突破后回抽或反抽、持有期和样本量；失败率会随目标阈值变化。", ("必须按市场状态、流动性和时期重新分层。",)),
    PdfRuleSeed("回抽反抽是条件变量而非必然确认", "bulkowski", (31, 34, 42), "突破后的回抽或反抽并非必然增强形态，书中样本显示它可能伴随较弱表现；系统应把是否回抽、耗时和回抽完成条件作为可检验变量，而非主观加分项。", ("原书统计必须用 A 股点时样本复验。",)),
    PdfRuleSeed("测量规则只是目标假设", "bulkowski", (46, 47), "以形态高度投射目标价的测量规则只能产生研究目标：向上突破时加到突破参考价，向下突破时扣除；实际结果必须统计目标命中率和失败分布。", ("不同形态的基准点不同，不得套用同一公式。", "目标不能替代止损。")),
)


def bootstrap_reviewed_stock_book_cards(
    manifests: dict[str, Path],
    knowledge_root: Path,
    *,
    reviewer: str = "Codex Knowledge QA",
    dry_run: bool = False,
) -> dict[str, list[str]]:
    payloads: dict[str, dict] = {}
    for key, path in manifests.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("source_type") != "pdf" or payload.get("citation_locator") != "pdf_page_index":
            raise KnowledgeError(f"{key} 不是逐页 PDF manifest")
        payloads[key] = payload
    repository = FileKnowledgeRepository(knowledge_root)
    existing_titles = {card.get("title") for card in repository.list_cards()}
    result: dict[str, list[str]] = {"published": [], "skipped": []}
    for seed in PDF_STOCK_RULE_SEEDS:
        if seed.title in existing_titles:
            result["skipped"].append(seed.title)
            continue
        manifest = manifests[seed.source]
        payload = payloads[seed.source]
        available = {int(page["page_number"]): page for page in payload.get("pages", []) if str(page.get("text", "")).strip()}
        missing = [page for page in seed.pages if page not in available]
        if missing:
            raise KnowledgeError(f"{seed.title} 缺少可核验 PDF 页: {missing}")
        if not dry_run:
            draft = repository.create_draft(
                title=seed.title,
                claim=seed.claim,
                source_case_id=None,
                evidence_refs=[EvidenceReference("pdf_page", str(manifest.resolve()), str(page - 1)) for page in seed.pages],
                research_artifacts=[],
                limitations=list(seed.limitations),
            )
            card_id = json.loads(draft.read_text(encoding="utf-8"))["card_id"]
            repository.review(card_id, reviewer, "publish", "已逐页核对定义、确认条件与限制；仅作为可复验规则起点，统计结论不得直接外推至 A 股。")
        result["published"].append(seed.title)
    return result
