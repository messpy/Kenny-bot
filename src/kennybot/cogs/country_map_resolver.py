import asyncio
import logging
from typing import Literal, Optional
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

ResolveStatus = Literal["found", "not_found", "transient"]


class CountryMapResolver:
    """
    国名からWikimedia Commonsの位置図URLを取得する。

    特徴:
    - Wikimedia APIは使用しない
    - Special:Redirect/file を利用
    - 非同期(async/await)
    - 成功結果をキャッシュ
    - 明確な404/410のみnegative cache
    - 403 / 429 / 5xx / timeout等はnegative cacheしない
    - 最終的な upload.wikimedia.org の画像URLを返す
    - HEADが403/405/501の場合はGETへフォールバック
    - cache_keyごとのLockは保持したまま解放しない
      (国旗クイズ用途では既知の国のみ・cache_key数が有限なため、
       waiter競合を避けるシンプルな設計を優先。
       将来ユーザーの自由入力に対応する場合は、
       参照カウント方式でのLock cleanupやTTL/LRUキャッシュを検討する)

    使用例:
        async with CountryMapResolver() as resolver:
            url = await resolver.get_map_url(
                "Japan",
                region_name="Asia",
            )

            if url:
                print(url)
            else:
                print("地図が見つかりませんでした")
    """

    BASE_REDIRECT_URL = (
        "https://commons.wikimedia.org/wiki/Special:Redirect/file/"
    )

    DEFAULT_USER_AGENT = "KennyBot/1.0 (https://github.com/kennypi/Kenny-bot; bot-traffic-compatible)"
    DEFAULT_TIMEOUT_SECONDS = 5

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self.timeout_seconds = timeout_seconds

        self._cache: dict[str, Optional[str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "CountryMapResolver":
        await self._ensure_session()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.timeout_seconds
            )

            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": self.user_agent,
                },
                timeout=timeout,
            )

        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

        self._session = None

    @staticmethod
    def _normalize_component(name: str) -> str:
        """
        Commonsのファイル名用に最低限の正規化だけ行う。

        大文字小文字やアポストロフィ、ハイフンなどは変更せず、
        空白のみ "_" に変換する。

        例:
            "Japan"         -> "Japan"
            "North America" -> "North_America"
            "Côte d'Ivoire" -> "Côte_d'Ivoire"
        """
        return "_".join(name.strip().split())

    def _generate_patterns(
        self,
        country_name: str,
        region_name: Optional[str] = None,
    ) -> list[str]:
        """
        Wikimedia Commonsで試行する位置図ファイル名候補を生成する。
        """
        country = self._normalize_component(country_name)

        patterns: list[str] = []

        if region_name:
            region = self._normalize_component(region_name)

            patterns.append(
                f"{country}_on_the_globe_({region}_centered).svg"
            )

        patterns.extend(
            [
                f"{country}_on_the_globe.svg",
                f"{country}_(orthographic_projection).svg",
            ]
        )

        return list(dict.fromkeys(patterns))

    @staticmethod
    def _is_valid_final_image_url(
        response: aiohttp.ClientResponse,
    ) -> bool:
        """
        最終リダイレクト先がWikimediaの画像URLであることを確認する。
        """
        if response.status != 200:
            return False

        if response.url.host != "upload.wikimedia.org":
            return False

        content_type = response.headers.get(
            "Content-Type",
            "",
        ).lower()

        if not content_type.startswith("image/"):
            return False

        return True

    async def _request_once(
        self,
        session: aiohttp.ClientSession,
        target_url: str,
        method: str,
    ) -> tuple[ResolveStatus, Optional[str], int]:
        """
        HTTPリクエストを1回実行する。

        found:
            有効な画像URLを取得

        not_found:
            404/410で候補ファイルが存在しない

        transient:
            403 / 429 / 5xx / timeout等
        """
        try:
            async with session.request(
                method,
                target_url,
                allow_redirects=True,
            ) as response:
                status = response.status

                if self._is_valid_final_image_url(response):
                    return (
                        "found",
                        str(response.url),
                        status,
                    )

                if status in (404, 410):
                    return (
                        "not_found",
                        None,
                        status,
                    )

                if status in (405, 501):
                    return (
                        "transient",
                        None,
                        status,
                    )

                if status == 403:
                    logger.warning(
                        "[ERROR] Wikimedia access rejected status=403 url=%s",
                        target_url,
                    )

                    return (
                        "transient",
                        None,
                        status,
                    )

                if status == 429:
                    logger.warning(
                        "[ERROR] Wikimedia rate limited request: %s",
                        target_url,
                    )

                    return (
                        "transient",
                        None,
                        status,
                    )

                if 500 <= status <= 599:
                    logger.warning(
                        "[ERROR] Wikimedia server error status=%d url=%s",
                        status,
                        target_url,
                    )

                    return (
                        "transient",
                        None,
                        status,
                    )

                if 400 <= status <= 499:
                    logger.warning(
                        "[ERROR] Wikimedia client error status=%d url=%s",
                        status,
                        target_url,
                    )

                    return (
                        "transient",
                        None,
                        status,
                    )

                logger.warning(
                    "[ERROR] Unexpected response status=%d final_url=%s",
                    status,
                    response.url,
                )

                return (
                    "transient",
                    None,
                    status,
                )

        except asyncio.TimeoutError:
            logger.warning(
                "[ERROR] Wikimedia request timeout: %s",
                target_url,
            )

            return (
                "transient",
                None,
                0,
            )

        except aiohttp.ClientError as exc:
            logger.warning(
                "[ERROR] Wikimedia request failed url=%s error=%s",
                target_url,
                exc,
            )

            return (
                "transient",
                None,
                0,
            )

    async def _resolve_candidate(
        self,
        session: aiohttp.ClientSession,
        filename: str,
        image_width: int,
    ) -> tuple[ResolveStatus, Optional[str]]:
        """
        Commonsファイル名候補を1つ解決する。

        通常: HEAD
        HEAD拒否/非対応: GET
        429や5xx等の場合はGETで追撃しない。
        """
        encoded_filename = quote(
            filename,
            safe="()_'-.~",
        )

        target_url = (
            f"{self.BASE_REDIRECT_URL}"
            f"{encoded_filename}"
            f"?width={image_width}"
        )

        logger.debug(
            "[CHECK] Trying Wikimedia candidate: %s",
            filename,
        )

        status, final_url, http_status = await self._request_once(
            session,
            target_url,
            "HEAD",
        )

        if status == "found":
            return (
                "found",
                final_url,
            )

        if status == "not_found":
            return (
                "not_found",
                None,
            )

        if http_status in (403, 405, 501):
            logger.debug(
                "[CHECK] HEAD rejected or unsupported; retrying with GET: %s",
                filename,
            )

            status, final_url, _ = await self._request_once(
                session,
                target_url,
                "GET",
            )

            return (
                status,
                final_url,
            )

        return (
            "transient",
            None,
        )

    async def get_map_url(
        self,
        country_name: str,
        region_name: Optional[str] = None,
        image_width: int = 960,
    ) -> Optional[str]:
        """
        国名・地域名からWikimedia Commonsの位置図を取得する。

        :param country_name: 正式な英語国名（例: "Japan", "Bhutan"）
        :param region_name: Commonsの地図中心名（例: "Asia", "Europe"）
        :param image_width: Wikimediaに要求するサムネイル幅
        :return: 成功時は upload.wikimedia.org の最終画像URL、
                 見つからない/一時エラー時は None
        """

        # ------------------------------------------------------------
        # 1. 状態確認 [CHECK]
        # ------------------------------------------------------------

        logger.debug("[CHECK] Validating map request")

        if not isinstance(country_name, str):
            logger.error("[ERROR] country_name must be str")
            return None

        country_name = country_name.strip()

        if not country_name:
            logger.error("[ERROR] country_name is empty")
            return None

        if region_name is not None:
            if not isinstance(region_name, str):
                logger.error("[ERROR] region_name must be str or None")
                return None

            region_name = region_name.strip()

            if not region_name:
                region_name = None

        if not isinstance(image_width, int):
            logger.error("[ERROR] image_width must be int")
            return None

        if image_width < 64 or image_width > 4096:
            logger.error(
                "[ERROR] image_width out of range: %d",
                image_width,
            )
            return None

        normalized_country = self._normalize_component(country_name)
        normalized_region = (
            self._normalize_component(region_name) if region_name else ""
        )

        cache_key = (
            f"{normalized_country}:"
            f"{normalized_region}:"
            f"{image_width}"
        )

        # ------------------------------------------------------------
        # 2. 想定状態判定
        # ------------------------------------------------------------

        logger.debug(
            "[CHECK] Resolving map country=%s region=%s width=%d",
            country_name,
            region_name,
            image_width,
        )

        if cache_key in self._cache:
            cached = self._cache[cache_key]

            logger.debug(
                "[INFO] Map cache hit country=%s result=%s",
                country_name,
                cached,
            )

            return cached

        # cache_keyごとのLockは再利用し、明示的には破棄しない。
        # (waiterが存在するかどうかをlocked()だけで判定できないため、
        #  Lock自体を残す方が安全。詳細はクラスdocstring参照)
        lock = self._locks.setdefault(
            cache_key,
            asyncio.Lock(),
        )

        async with lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

            # --------------------------------------------------------
            # 3. 処理実行 [ACTION]
            # --------------------------------------------------------

            session = await self._ensure_session()

            patterns = self._generate_patterns(
                country_name,
                region_name,
            )

            logger.debug(
                "[ACTION] Trying %d Wikimedia patterns for %s",
                len(patterns),
                country_name,
            )

            saw_transient_error = False

            for filename in patterns:
                status, final_url = await self._resolve_candidate(
                    session,
                    filename,
                    image_width,
                )

                if status == "found" and final_url:
                    logger.info(
                        "[INFO] Resolved map country=%s file=%s url=%s",
                        country_name,
                        filename,
                        final_url,
                    )

                    self._cache[cache_key] = final_url

                    # ------------------------------------------------
                    # 4. 結果確認 [CHECK]
                    # ------------------------------------------------

                    logger.debug("[CHECK] Final map URL=%s", final_url)

                    return final_url

                if status == "transient":
                    saw_transient_error = True

            # --------------------------------------------------------
            # 4. 結果確認 [CHECK]
            # --------------------------------------------------------

            if saw_transient_error:
                logger.warning(
                    "[ERROR] Map resolution incomplete due to temporary "
                    "Wikimedia error country=%s region=%s",
                    country_name,
                    region_name,
                )

                # 一時障害はキャッシュしない
                return None

            logger.warning(
                "[CHECK] No Wikimedia map found "
                "country=%s region=%s tried=%d",
                country_name,
                region_name,
                len(patterns),
            )

            # 全候補が404/410だった場合のみnegative cache
            self._cache[cache_key] = None

            return None


async def _resolve_test_suite(
    resolver: "CountryMapResolver",
    tests: list[tuple[str, str]],
) -> None:
    """
    複数国のURL解決率テスト。

    同一cache_keyへの同時アクセスもあわせて確認するため、
    先頭のケースだけ意図的に並行実行する。
    """
    if tests:
        first_country, first_region = tests[0]

        logger.info(
            "[CHECK] Testing concurrent access for same cache_key: %s",
            first_country,
        )

        results = await asyncio.gather(
            resolver.get_map_url(first_country, first_region),
            resolver.get_map_url(first_country, first_region),
            resolver.get_map_url(first_country, first_region),
        )

        logger.info(
            "[INFO] Concurrent results consistent: %s",
            len(set(results)) == 1,
        )

    success = 0
    failure = 0

    for country, region in tests:
        url = await resolver.get_map_url(
            country_name=country,
            region_name=region,
            image_width=960,
        )

        if url is None:
            failure += 1
            print(
                f"[ERROR] country={country} "
                f"region={region} 地図を取得できませんでした"
            )
            continue

        success += 1
        print(f"[INFO] country={country} region={region}")
        print(f"[CHECK] resolved_url={url}")

    total = success + failure
    rate = (success / total * 100) if total else 0.0

    print(
        f"[CHECK] 解決率: {success}/{total} ({rate:.1f}%)"
    )


async def main() -> None:
    """
    単体動作確認。

    Discord Botへ組み込む場合はmain()部分を削除して
    CountryMapResolverだけ利用する。
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    print("[CHECK] CountryMapResolver 動作確認")
    print("[CHECK] aiohttpセッションを作成します")

    tests = [
        ("Bhutan", "Asia"),
        ("Nepal", "Asia"),
        ("Japan", "Asia"),
        ("Tuvalu", "Oceania"),
        ("Monaco", "Europe"),
    ]

    print("[INFO] 想定状態: Wikimedia CommonsへHTTPS接続可能")
    print("[ACTION] 地図URLを取得します")

    async with CountryMapResolver() as resolver:
        await _resolve_test_suite(resolver, tests)

    print("[CHECK] 動作確認終了")


if __name__ == "__main__":
    asyncio.run(main())
