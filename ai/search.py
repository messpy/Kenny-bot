from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from ddgs import DDGS  # uv add ddgs
from ddgs.exceptions import TimeoutException, DDGSException

logger = logging.getLogger(__name__)

# モード別の最大全文長（最終まとめ用）
_MODE_TO_MAX_CHARS: Dict[str, int] = {
    "short": 800,
    "normal": 1600,
    "long": 3000,
}

# ============================================================
# 設定クラス
# ============================================================

@dataclass
class SearchConfig:
    """DuckDuckGo 検索設定"""

    top_n: int = 3              # 返信で使う件数
    max_results: int = 10       # ddgs から取る最大件数
    timelimit: str = "w"        # d/w/m/y
    region: str = "jp-jp"       # 日本ローカル向け
    safesearch: str = "moderate"
    news_only: bool = True      # True: news のみ / False: news + text
    # 旧実装互換用（DummySearcher / 古い main.py からも呼ばれる）
    prefer_news: bool = True

    def __post_init__(self) -> None:
        # prefer_news を優先的に news_only に反映（後方互換用）
        self.news_only = bool(self.prefer_news)


@dataclass
class SummaryConfig:
    """記事1件ごとの要約設定"""

    mode: str = "normal"
    concurrency: int = 2
    model: str = "gemma2:2b"    # 記事要約に使うモデル
    max_chars: int = 400        # 1件あたりの要約最大文字数
    fallback_models: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class WebItem:
    """検索結果 1 件分"""

    title: str
    url: str
    snippet: str
    date: Optional[str] = None
    source: Optional[str] = None


@dataclass
class AISearchAnswer:
    """最終的に bot 側に返す結果"""

    query: str
    items: List[WebItem]
    summaries: List[str]
    answer: str  # Discord にそのまま投げる本文


# ============================================================
# DuckDuckGo 検索ラッパ
# ============================================================

class DuckDuckGoSearch:
    def __init__(self, config: SearchConfig) -> None:
        # 念のため旧フィールドとの互換（__post_init__ でも反映しているので保険）
        if hasattr(config, "prefer_news"):
            config.news_only = bool(getattr(config, "prefer_news"))
        self.config = config

    def _to_item(self, r: dict, *, source: str) -> WebItem:
        title = r.get("title") or r.get("heading") or "(no title)"
        url = r.get("url") or r.get("href") or ""
        snippet = r.get("body") or r.get("snippet") or ""
        date = r.get("date") or r.get("published") or None
        return WebItem(
            title=title,
            url=url,
            snippet=snippet,
            date=date,
            source=source,
        )

    def search(self, query: str, *, news_only: Optional[bool] = None) -> List[WebItem]:
        """
        DuckDuckGo で Web 検索を行う（同期関数）。

        news_only:
          - None: config.news_only に従う
          - True: ニュースのみ
          - False: ニュース + 通常 Web 検索
        """
        cfg = self.config
        use_news_only = cfg.news_only if news_only is None else news_only
        items: List[WebItem] = []

        logger.info("[search] ddgs search start: query=%r news_only=%s", query, use_news_only)

        with DDGS() as ddgs:
            # ---- ニュース検索 ----
            try:
                for r in ddgs.news(
                    query,
                    region=cfg.region,
                    safesearch=cfg.safesearch,
                    timelimit=cfg.timelimit,
                    max_results=cfg.max_results,
                ):
                    try:
                        it = self._to_item(r, source="news")
                        items.append(it)
                    except Exception as e:
                        logger.debug("[search] skip invalid news item: %r (%r)", r, e)
                    if len(items) >= cfg.max_results:
                        break
            except TimeoutException as e:
                logger.warning("[search] ddgs.news timeout: %r", e)
                raise
            except DDGSException as e:
                # 「No results found.」だけは 0 件扱いにしておく
                if "No results found" in str(e):
                    logger.info("[search] ddgs.news no results: %r", e)
                else:
                    logger.warning("[search] ddgs.news error: %r", e)
                    raise
            except Exception as e:
                logger.warning("[search] ddgs.news unexpected error: %r", e)

            # ---- 通常 Web 検索 ----
            if (not use_news_only) and len(items) < cfg.max_results:
                seen_urls = {it.url for it in items}
                try:
                    for r in ddgs.text(
                        query,
                        region=cfg.region,
                        safesearch=cfg.safesearch,
                        timelimit=cfg.timelimit,
                        max_results=cfg.max_results,
                    ):
                        try:
                            it = self._to_item(r, source="web")
                            if it.url and it.url in seen_urls:
                                continue
                            items.append(it)
                            if it.url:
                                seen_urls.add(it.url)
                        except Exception as e:
                            logger.debug("[search] skip invalid web item: %r (%r)", r, e)
                        if len(items) >= cfg.max_results:
                            break
                except TimeoutException as e:
                    logger.warning("[search] ddgs.text timeout: %r", e)
                    raise
                except DDGSException as e:
                    if "No results found" in str(e):
                        logger.info("[search] ddgs.text no results: %r", e)
                    else:
                        logger.warning("[search] ddgs.text error: %r", e)
                        raise
                except Exception as e:
                    logger.warning("[search] ddgs.text unexpected error: %r", e)

        logger.info("[search] ddgs search done: query=%r items=%d", query, len(items))
        return list(items)


# ============================================================
# 要約・AI まとめ
# ============================================================

class OllamaRunner:
    """
    main.py 側で使っている runner と互換のためのプロトタイプクラス。
    実装は main.py 側に委譲される想定。
    """
    async def run_async(self, prompt: str, *, model: str) -> str:  # pragma: no cover
        raise NotImplementedError


class WebSummarizer:
    def __init__(self, runner: OllamaRunner, config: SummaryConfig) -> None:
        self.runner = runner
        self.config = config
        self._sem = asyncio.Semaphore(config.concurrency)

    def _candidate_models(self) -> list[str]:
        candidates: list[str] = []
        for model in (self.config.model, *self.config.fallback_models):
            model_name = str(model or "").strip()
            if not model_name or model_name in candidates:
                continue
            candidates.append(model_name)
        return candidates

    async def summarize_one(self, question: str, item: WebItem, *, mode: str = "normal") -> str:
        """
        記事 1 件分を要約する。
        """
        base = (
            item.snippet
            if len(item.snippet) <= self.config.max_chars
            else item.snippet[: self.config.max_chars] + "..."
        )

        date_str = item.date or "日付情報なし"

        prompt = f"""あなたはニュース/記事の要約アシスタントです。
以下の記事について、ユーザーの質問に関連しそうなポイントを日本語で端的に要約してください。

【ユーザーの質問】
{question}

【記事タイトル】
{item.title}

【公開日/更新日】
{date_str}

【記事本文（抜粋）】
{base}

【出力条件】
・2〜4行程度の日本語要約
・事実ベースで、推測は避ける
・記事本文（抜粋）と記事要約一覧に書かれていない固有名詞、数値、出来事は追加しない
・質問に対する答えが記事から確認できない場合は、確認できないと書く
・見出しや装飾は不要
"""

        async with self._sem:
            last_error: Exception | None = None
            for model_name in self._candidate_models():
                try:
                    out = await self.runner.run_async(prompt, model=model_name)
                    out = (out or "").strip()
                    if len(out) > self.config.max_chars:
                        out = out[:self.config.max_chars] + "..."
                    if out:
                        return out
                except Exception as e:
                    last_error = e
                    logger.warning(
                        "[ai_search] summarize failed with model=%s url=%s: %r",
                        model_name,
                        item.url,
                        e,
                    )
                    continue
            if last_error is not None:
                logger.warning(
                    "[ai_search] summarize failed for all models url=%s: %r",
                    item.url,
                    last_error,
                )
        return ""


class AISearchService:
    def __init__(
        self,
        searcher: DuckDuckGoSearch,
        summarizer: WebSummarizer,
        runner: OllamaRunner,
        *,
        final_model: str,
        final_fallback_models: Optional[List[str]] = None,
        mode_to_max_chars: Optional[Dict[str, int]] = None,
        debug: bool = False,
    ) -> None:
        self.searcher = searcher
        self.summarizer = summarizer
        self.runner = runner
        self.final_model = final_model
        self.final_fallback_models = [
            str(model or "").strip()
            for model in (final_fallback_models or [])
            if str(model or "").strip()
        ]
        self._mode_to_max_chars = mode_to_max_chars or _MODE_TO_MAX_CHARS
        self.debug = debug

    # ------------------------------
    # クエリ生成・モード判定
    # ------------------------------
    def _build_query(self, question: str) -> str:
        q = question.strip()
        q = q.replace("\n", " ")
        return q

    def _prefer_web_over_news(self, question: str) -> bool:
        """
        「意味」「とは」「定義」「由来」「語源」などを含む場合は
        ニュース限定ではなく通常 Web 検索も有効にする。
        """
        keywords = ("意味", "とは", "定義", "由来", "語源")
        return any(k in question for k in keywords)

    def _build_direct_answer(
        self,
        *,
        question: str,
        query: str,
        items: List[WebItem],
        summaries: List[str],
    ) -> str:
        if not items:
            return "検索結果が取得できませんでした。キーワードを変えて再度お試しください。"

        def _snippet_summary(item: WebItem) -> str:
            snippet = (item.snippet or "").strip()
            if snippet:
                snippet = snippet.replace("\n", " ")
                if len(snippet) > 180:
                    snippet = snippet[:180] + "..."
            title = (item.title or "").strip()
            if snippet and title:
                return f"{title}。{snippet}"
            if snippet:
                return snippet
            if title:
                return title
            return "詳細は記事URLを確認してください。"

        lines: List[str] = []
        lines.append("Web検索結果を取得しました。")
        lines.append("")
        lines.append("【要点】")
        for item in items[: self.searcher.config.top_n]:
            lines.append(f"- {_snippet_summary(item)}")
        if summaries:
            lines.append("")
            lines.append("【AI要約】")
            for sm in summaries[: self.searcher.config.top_n]:
                lines.append(f"- {sm}")
        lines.append("")
        lines.append("【見つかった記事】")
        for it in items[: self.searcher.config.top_n]:
            date_str = f"（{it.date}）" if it.date else ""
            snippet = f" / {it.snippet}" if it.snippet else ""
            lines.append(f"- {it.title}{date_str}{snippet}\n  {it.url}")
        lines.append("")
        lines.append("【参考】")
        for it in items[: self.searcher.config.top_n]:
            lines.append(f"- {it.url}")
        return "\n".join(lines)

    def _candidate_final_models(self) -> list[str]:
        candidates: list[str] = []
        for model in (self.final_model, *self.final_fallback_models):
            model_name = str(model or "").strip()
            if not model_name or model_name in candidates:
                continue
            candidates.append(model_name)
        return candidates

    async def _run_with_model_fallbacks(self, prompt: str) -> str:
        last_error: Exception | None = None
        for model_name in self._candidate_final_models():
            try:
                answer_text = await self.runner.run_async(prompt, model=model_name)
                return (answer_text or "").strip()
            except Exception as e:
                last_error = e
                logger.warning(
                    "[ai_search] final generation failed with model=%s: %r",
                    model_name,
                    e,
                )
                continue
        if last_error is not None:
            raise last_error
        return ""

    # ------------------------------
    # メイン処理
    # ------------------------------
    async def answer_ai_async(
        self,
        question: str,
        *,
        mode: str = "normal",
        news_only: bool | None = None,
    ) -> AISearchAnswer:
        q = self._build_query(question)
        if self.debug:
            logger.debug("[ai_search] question=%r query=%r mode=%s", question, q, mode)
        else:
            logger.info("[ai_search] built query: %r", q)

        # 定義系の質問なら news_only=False で検索。呼び出し側が明示した場合はそれを優先する。
        prefer_web = self._prefer_web_over_news(question)
        effective_news_only = (not prefer_web) if news_only is None else bool(news_only)

        try:
            items = await asyncio.to_thread(
                self.searcher.search,
                q,
                news_only=effective_news_only,
            )
        except TimeoutException as e:
            logger.warning("[ai_search] ddgs timeout: %r", e)
            msg = "Web検索の実行に失敗しました。クエリを短くするか、時間をおいて再度お試しください。"
            return AISearchAnswer(query=q, items=[], summaries=[], answer=msg)
        except DDGSException as e:
            logger.warning("[ai_search] ddgs error: %r", e)
            msg = "Web検索の実行に失敗しました。クエリを短くするか、時間をおいて再度お試しください。"
            return AISearchAnswer(query=q, items=[], summaries=[], answer=msg)
        except Exception as e:
            logger.exception("[ai_search] unexpected search error")
            msg = "Web検索で予期しないエラーが発生しました。時間をおいて再度お試しください。"
            return AISearchAnswer(query=q, items=[], summaries=[], answer=msg)

        if not items:
            msg = "検索結果が取得できませんでした。キーワードを変えて再度お試しください。"
            return AISearchAnswer(query=q, items=[], summaries=[], answer=msg)

        top_n = self.searcher.config.top_n
        targets = items[: max(top_n * 2, top_n)]  # 失敗に備えて多めに要約

        tasks = [self.summarizer.summarize_one(question, it, mode=mode) for it in targets]
        raw_summaries = await asyncio.gather(*tasks, return_exceptions=True)

        summaries: List[str] = []
        used_items: List[WebItem] = []
        for it, res in zip(targets, raw_summaries):
            if isinstance(res, Exception):
                logger.warning("[ai_search] summarize error for url=%s: %r", it.url, res)
                continue
            text = (str(res) or "").strip()
            if not text:
                continue
            summaries.append(text)
            used_items.append(it)
            if len(summaries) >= top_n:
                break

        if not summaries:
            fallback_items = items[:top_n]
            answer = self._build_direct_answer(
                question=question,
                query=q,
                items=fallback_items,
                summaries=[],
            )
            return AISearchAnswer(query=q, items=fallback_items, summaries=[], answer=answer)

        # Discord 向けにまとめるためのプロンプトを構築
        overall_prompt_parts: List[str] = []
        for idx, (it, sm) in enumerate(zip(used_items, summaries), start=1):
            date_str = it.date or "日付情報なし"
            overall_prompt_parts.append(
                f"【記事{idx}】\n"
                f"タイトル: {it.title}\n"
                f"日付: {date_str}\n"
                f"要約: {sm}\n"
            )

        overall_prompt = "\n".join(overall_prompt_parts)

        final_prompt = f"""あなたはWeb検索結果をもとに回答する日本語アシスタントです。
以下はユーザーの質問と、それに関連すると判断されたニュース/記事の要約一覧です。
事実ベースで、分かりやすく、必要に応じて箇条書きで説明してください。

【ユーザーの質問】
{question}

【検索クエリ】
{q}

【記事要約一覧】
{overall_prompt}

【出力フォーマット（必ずこの形式で）】
1. まず「AI要約 まとめ」として、全体の結論やポイントを2〜5行でまとめる
2. そのあと空行を入れて、各記事ごとに以下の形式で列挙する:
   - 行1: 「記事タイトル 」+ タイトル + スペース + 公開日/更新日（不明なら「日付情報なし」）
   - 行2: 「記事の内容 要約 」+ その記事の要約（2〜4行程度）
3. 最後に [参考] セクションを作り、利用した記事の URL を上から順に列挙する

※ 検索結果にない情報は推測しないでください。
※ 挨拶文、雑談、ユーザー名の言い回し、日付の補完、ニュースの創作はしないでください。
※ 記事から確認できない話題は「検索結果からは確認できません」と書いてください。
"""

        try:
            answer_text = await self._run_with_model_fallbacks(final_prompt)
        except Exception as e:
            logger.warning("[ai_search] final answer generation failed: %r", e)
            answer_text = ""

        if not answer_text:
            answer_text = self._build_direct_answer(
                question=question,
                query=q,
                items=used_items,
                summaries=summaries,
            )

        max_chars = self._mode_to_max_chars.get(mode, _MODE_TO_MAX_CHARS["normal"])
        if len(answer_text) > max_chars:
            answer_text = answer_text[:max_chars] + "\n...(省略)..."

        return AISearchAnswer(query=q, items=used_items, summaries=summaries, answer=answer_text)


# 旧名との互換用（main.py は Summarizer を import している）
Summarizer = WebSummarizer
