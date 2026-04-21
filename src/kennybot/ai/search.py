from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from typing import List, Optional, Dict

from ddgs import DDGS  # uv add ddgs
from ddgs.exceptions import TimeoutException, DDGSException
from src.kennybot.utils.text import normalize_keyword_match_text

logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))

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
    searched_queries: List[str]
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
        raw = normalize_keyword_match_text(question or "")
        raw = raw.replace("\n", " ")
        raw = raw.replace("　", " ")
        raw = raw.strip()
        if not raw:
            return ""

        raw = re.sub(
            r"(ちょっとググってみます。?|ググってみます。?|待っててね。?|待ってて。?)",
            " ",
            raw,
        )
        raw = re.sub(
            r"(バグかな。?|もう一回聞くよ。?|いまさらな質問なんだけど。?|質問なんだけど。?)",
            " ",
            raw,
        )

        ascii_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._+-]*", raw)
        jp_tokens = re.findall(r"[ぁ-んァ-ヶー一-龥]+", raw)

        stop_words = {
            "の",
            "は",
            "が",
            "を",
            "に",
            "へ",
            "で",
            "と",
            "や",
            "か",
            "だ",
            "です",
            "ます",
            "ね",
            "よ",
            "かな",
            "かも",
            "って",
            "だけ",
            "こと",
            "もの",
            "今日",
            "きょう",
            "今",
            "いま",
            "最近",
            "最新",
            "調べて",
            "しらべて",
            "検索",
            "検索して",
            "探して",
            "ググってみる",
            "ググる",
            "聞く",
            "聞いて",
            "もう一回",
            "一回",
            "私",
            "自分",
            "あなた",
            "さん",
            "ちゃん",
            "まだ",
            "しか",
            "バグ",
            "質問",
            "いまさら",
            "もう",
            "一度",
            "再度",
            "確認",
            "確認中",
        }
        noisy_jp_fragments = (
            "したこと",
            "なんだけど",
            "だったりする",
            "っていう",
            "という",
            "ってこと",
            "ついている",
            "使ったことない",
            "わざわざ",
            "あって",
            "あるんだけど",
            "みたい",
        )

        terms: list[str] = []
        for token in ascii_tokens:
            item = token.strip().casefold()
            if len(item) < 2 or item in stop_words:
                continue
            if item not in terms:
                terms.append(item)

        for token in jp_tokens:
            item = token.strip()
            if not item or item in stop_words:
                continue
            if len(item) > 8:
                continue
            if any(fragment in item for fragment in noisy_jp_fragments):
                continue
            if item not in terms:
                terms.append(item)

        if terms:
            return " ".join(terms[:4])
        return raw[:60]

    def _prefer_web_over_news(self, question: str) -> bool:
        """
        「意味」「とは」「定義」「由来」「語源」や、
        ソフトウェア/CLI/GUI の確認はニュース限定ではなく通常 Web 検索も有効にする。
        """
        keywords = (
            "意味",
            "とは",
            "定義",
            "由来",
            "語源",
            "CLI",
            "GUI",
            "アプリ",
            "ソフト",
            "ツール",
            "バージョン",
            "version",
            "API",
            "GitHub",
            "OpenAI",
            "Codex",
        )
        return any(k in question for k in keywords)

    def _query_terms(self, query: str) -> list[str]:
        normalized = normalize_keyword_match_text(query or "")
        raw_terms = re.split(r"[\s\u3000\W_]+", normalized)
        stop_words = {
            "の",
            "は",
            "が",
            "を",
            "に",
            "へ",
            "で",
            "と",
            "や",
            "か",
            "だ",
            "です",
            "ます",
            "ね",
            "よ",
            "今日",
            "きょう",
            "今",
            "いま",
            "最近",
            "最新",
            "もう",
            "一回",
            "回",
        }
        terms: list[str] = []
        for term in raw_terms:
            item = term.strip()
            if not item or item in stop_words:
                continue
            if item not in terms:
                terms.append(item)
        return terms[:8]

    def _url_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url or "")
            host = (parsed.netloc or "").strip().casefold()
            if host.startswith("www."):
                host = host[4:]
            return host
        except Exception:
            return ""

    def _official_domain_hints(self, query: str) -> list[str]:
        normalized = normalize_keyword_match_text(query or "")
        hints: list[str] = []

        def add(*domains: str) -> None:
            for domain in domains:
                item = domain.strip().casefold()
                if item and item not in hints:
                    hints.append(item)

        if any(key in normalized for key in ("openai", "codex")):
            add("openai.com", "platform.openai.com", "docs.openai.com", "github.com")
        if "github" in normalized:
            add("github.com", "docs.github.com", "github.blog")
        if any(key in normalized for key in ("microsoft", "azure")):
            add("microsoft.com", "learn.microsoft.com", "azure.microsoft.com")
        if "apple" in normalized:
            add("apple.com", "support.apple.com", "developer.apple.com")
        if "google" in normalized:
            add("google.com", "developers.google.com", "cloud.google.com")
        if "anthropic" in normalized or "claude" in normalized:
            add("anthropic.com", "docs.anthropic.com")
        if "meta" in normalized or "llama" in normalized:
            add("meta.com", "ai.meta.com", "github.com")
        return hints

    def _official_domain_bonus(self, query: str, url: str) -> int:
        domain = self._url_domain(url)
        if not domain:
            return 0
        hints = self._official_domain_hints(query)
        if not hints:
            return 0
        if any(domain == hint or domain.endswith(f".{hint}") for hint in hints):
            return 6
        if any(hint in domain for hint in hints):
            return 3
        return 0

    def _prefer_text_first(self, query: str) -> bool:
        normalized = normalize_keyword_match_text(query or "")
        keywords = (
            "CLI",
            "GUI",
            "API",
            "docs",
            "documentation",
            "公式",
            "OpenAI",
            "GitHub",
            "Codex",
            "model",
            "tool",
            "app",
            "アプリ",
            "インストール",
            "使い方",
            "設定",
            "version",
            "バージョン",
        )
        return any(keyword.casefold() in normalized for keyword in keywords)

    def _search_query_variants(self, query: str) -> list[str]:
        normalized = query.strip()
        variants: list[str] = []

        def add(candidate: str) -> None:
            candidate = candidate.strip()
            if candidate and candidate not in variants:
                variants.append(candidate)

        add(normalized)
        if normalized:
            add(f"{normalized} +公式")
            add(f"{normalized} +official")
            add(f"{normalized} +docs")
            add(f"{normalized} +documentation")
            for domain in self._official_domain_hints(normalized):
                add(f"{normalized} site:{domain}")
                add(f"{normalized} +公式 site:{domain}")
        return variants[:6]

    def _result_alignment_score(self, query: str, item: WebItem) -> int:
        terms = self._query_terms(query)
        if not terms:
            return 0
        title = normalize_keyword_match_text(item.title or "")
        snippet = normalize_keyword_match_text(item.snippet or "")
        score = 0
        for term in terms:
            if term in title:
                score += 3
            if term in snippet:
                score += 1
        score += self._official_domain_bonus(query, item.url)
        return score

    def _looks_off_topic(self, query: str, items: list[WebItem]) -> bool:
        if not items:
            return True
        top_items = items[: min(len(items), 3)]
        scores = [self._result_alignment_score(query, item) for item in top_items]
        if not any(score >= 3 for score in scores):
            return True
        if any(self._official_domain_bonus(query, item.url) > 0 for item in top_items):
            return False
        return max(scores) < 4

    def _rank_items(self, query: str, items: List[WebItem]) -> List[WebItem]:
        terms = self._query_terms(query)
        if not terms:
            return list(items)
        scored: list[tuple[int, int, WebItem]] = []
        for idx, item in enumerate(items):
            score = self._result_alignment_score(query, item)
            if score > 0:
                scored.append((score, idx, item))
        if not scored:
            return list(items)
        scored.sort(key=lambda row: (-row[0], row[1]))
        ranked = [item for _, _, item in scored]
        ranked.extend(item for item in items if item not in ranked)
        return ranked

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
        lines.append("回答")
        lines.append(f"- {question.strip() or query.strip() or '質問'} に対して確認できた範囲では、以下の内容が有力です。")
        if summaries:
            for sm in summaries[: self.searcher.config.top_n]:
                lines.append(f"- {sm}")
        else:
            for item in items[: self.searcher.config.top_n]:
                lines.append(f"- {_snippet_summary(item)}")

        lines.append("")
        lines.append("補足")
        for item in items[: self.searcher.config.top_n]:
            lines.append(f"- {_snippet_summary(item)}")
        return "\n".join(lines)

    def _looks_like_structured_web_answer(self, text: str) -> bool:
        normalized = normalize_keyword_match_text(strip_ansi_and_ctrl(text or ""))
        return "回答" in normalized or "全体要約" in normalized

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

        items: list[WebItem] = []
        tried_queries: list[str] = []
        query_variants = self._search_query_variants(q)
        if self._prefer_text_first(q):
            query_variants = [
                *[candidate for candidate in query_variants if candidate not in {q}],
                q,
            ]

        last_error: Exception | None = None
        for idx, candidate_query in enumerate(query_variants):
            tried_queries.append(candidate_query)
            try:
                candidate_items = await asyncio.to_thread(
                    self.searcher.search,
                    candidate_query,
                    news_only=effective_news_only,
                )
            except TimeoutException as e:
                last_error = e
                logger.warning("[ai_search] ddgs timeout query=%r: %r", candidate_query, e)
                continue
            except DDGSException as e:
                last_error = e
                logger.warning("[ai_search] ddgs error query=%r: %r", candidate_query, e)
                continue
            except Exception as e:
                last_error = e
                logger.exception("[ai_search] unexpected search error query=%r", candidate_query)
                continue

            if not candidate_items:
                continue

            items = candidate_items
            if idx == 0 and self._looks_off_topic(q, items):
                continue
            if not self._looks_off_topic(candidate_query, items):
                break

        if not items:
            if last_error is not None:
                msg = "Web検索の実行に失敗しました。クエリを短くするか、時間をおいて再度お試しください。"
                return AISearchAnswer(query=q, searched_queries=[q], items=[], summaries=[], answer=msg)
            msg = "検索結果が取得できませんでした。キーワードを変えて再度お試しください。"
            return AISearchAnswer(query=q, searched_queries=[q], items=[], summaries=[], answer=msg)

        ranked_items = self._rank_items(q, items)
        top_n = self.searcher.config.top_n
        targets = ranked_items[: max(top_n * 2, top_n)]  # 失敗に備えて多めに要約

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
            fallback_items = ranked_items[:top_n]
            answer = self._build_direct_answer(
                question=question,
                query=q,
                items=fallback_items,
                summaries=[],
            )
            return AISearchAnswer(query=q, searched_queries=[q], items=fallback_items, summaries=[], answer=answer)

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
        today_jst = datetime.now(JST).strftime("%Y-%m-%d")
        current_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")

        final_prompt = f"""あなたはWeb検索結果を統合して質問に答える日本語アシスタントです。
以下はユーザーの質問と、それに関連すると判断されたニュース/記事の要約一覧です。
事実ベースで、分かりやすく、質問への答えを先に述べてください。
検索結果を単に並べるのではなく、質問に直接関係する情報だけを統合して答えてください。
冒頭の挨拶、雑談、検索実施の宣言、自己言及は書かないでください。
今の判断基準として、Today in JST is {today_jst} を使ってください。
Current time in JST is {current_jst}.
「今」「この時期」「季節」はこの日付基準で解釈してください。

【ユーザーの質問】
{question}

【検索クエリ】
{q}

【記事要約一覧】
{overall_prompt}

【出力フォーマット（必ずこの形式で）】
1. まず「回答」として、質問への答えを2〜5行でまとめる
2. 必要なら「補足」として、重要なポイントを箇条書きで最大5件まで書く

※ 検索結果にない情報は推測しないでください。
※ 挨拶文、雑談、ユーザー名の言い回し、日付の補完、ニュースの創作はしないでください。
※ 記事から確認できない話題は「検索結果からは確認できません」と書いてください。
※ 形式が崩れそうなら、本文は短くしてもよいので、必ず「回答」「補足」の順で出力してください。
"""

        try:
            answer_text = await self._run_with_model_fallbacks(final_prompt)
        except Exception as e:
            logger.warning("[ai_search] final answer generation failed: %r", e)
            answer_text = ""

        if not answer_text or not self._looks_like_structured_web_answer(answer_text):
            answer_text = self._build_direct_answer(
                question=question,
                query=q,
                items=used_items,
                summaries=summaries,
            )

        max_chars = self._mode_to_max_chars.get(mode, _MODE_TO_MAX_CHARS["normal"])
        if len(answer_text) > max_chars:
            answer_text = answer_text[:max_chars] + "\n...(省略)..."

        return AISearchAnswer(
            query=q,
            searched_queries=tried_queries or [q],
            items=used_items,
            summaries=summaries,
            answer=answer_text,
        )


# 旧名との互換用（main.py は Summarizer を import している）
Summarizer = WebSummarizer
