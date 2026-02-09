"""Thesis auto-validation via news: search, score, and transition theses based on headlines.

Searches Google News RSS for articles matching thesis validation and failure criteria,
scores them as supporting/neutral/contradicting, and automatically transitions thesis
status based on accumulated evidence.

Classes:
    NewsValidator: Main class for thesis validation via news headlines.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from db.database import Database
from engine import Signal, SignalAction, SignalSource, SignalStatus, Thesis, ThesisStatus
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}"
REQUEST_TIMEOUT = 15


class NewsValidator:
    """Validates theses against news headlines.

    Searches for news articles matching thesis validation/failure criteria,
    scores article sentiment, stores results, and auto-transitions thesis
    status based on accumulated evidence.

    Attributes:
        db: Database instance for persistence.
        thesis_engine: ThesisEngine for reading/transitioning theses.
        signal_engine: SignalEngine for generating sell signals on invalidation.
    """

    def __init__(
        self,
        db: Database,
        thesis_engine: ThesisEngine,
        signal_engine: SignalEngine,
    ) -> None:
        """Initialize the NewsValidator.

        Args:
            db: Database instance.
            thesis_engine: ThesisEngine for thesis CRUD and transitions.
            signal_engine: SignalEngine for creating SELL signals on invalidation.
        """
        self.db = db
        self.thesis_engine = thesis_engine
        self.signal_engine = signal_engine

    def search_news(self, thesis_id: int) -> list[dict]:
        """Search news for articles matching a thesis's criteria.

        Builds search queries from the thesis's validation_criteria and
        failure_criteria, fetches Google News RSS, and returns parsed articles.

        Args:
            thesis_id: ID of the thesis to search news for.

        Returns:
            List of article dicts with keys: title, url, source, published.
            Returns empty list if thesis not found or on any error.
        """
        thesis = self.thesis_engine.get_thesis(thesis_id)
        if not thesis:
            return []

        keywords = thesis.validation_criteria + thesis.failure_criteria
        if not keywords:
            # Fall back to title + symbols
            keywords = [thesis.title] + thesis.symbols

        articles: list[dict] = []
        seen_urls: set[str] = set()

        for keyword in keywords:
            try:
                fetched = self._fetch_rss(keyword)
                for article in fetched:
                    url = article.get("url", "")
                    if url not in seen_urls:
                        seen_urls.add(url)
                        articles.append(article)
            except Exception:
                logger.warning("Failed to fetch news for keyword: %s", keyword, exc_info=True)
                continue

        return articles

    def _fetch_rss(self, query: str) -> list[dict]:
        """Fetch and parse Google News RSS for a query.

        Args:
            query: Search query string.

        Returns:
            List of article dicts parsed from RSS items.
        """
        url = GOOGLE_NEWS_RSS.format(query=httpx.URL(query))
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MoneyMoves/1.0)"}

        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        return self._parse_rss(resp.text)

    def _parse_rss(self, xml_text: str) -> list[dict]:
        """Parse RSS XML into article dicts.

        Args:
            xml_text: Raw RSS XML string.

        Returns:
            List of article dicts with keys: title, url, source, published.
        """
        try:
            soup = BeautifulSoup(xml_text, "xml")
        except Exception:
            soup = BeautifulSoup(xml_text, "html.parser")
        articles: list[dict] = []

        for item in soup.find_all("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            source_el = item.find("source")
            pub_el = item.find("pubdate")

            title = title_el.get_text(strip=True) if title_el else ""
            link = link_el.get_text(strip=True) if link_el else ""
            source = source_el.get_text(strip=True) if source_el else ""
            published = pub_el.get_text(strip=True) if pub_el else ""

            if title:
                articles.append(
                    {
                        "title": title,
                        "url": link,
                        "source": source,
                        "published": published,
                    }
                )

        return articles

    def score_article(self, article: dict, thesis: Thesis) -> str:
        """Score an article's sentiment relative to a thesis.

        Checks the headline against validation_criteria (supporting) and
        failure_criteria (contradicting). If neither matches, returns 'neutral'.

        Args:
            article: Article dict with at least a 'title' key.
            thesis: Thesis model with validation_criteria and failure_criteria.

        Returns:
            One of 'supporting', 'neutral', 'contradicting'.
        """
        title_lower = article.get("title", "").lower()
        summary_lower = article.get("summary", "").lower()
        text = f"{title_lower} {summary_lower}".strip()

        # Check failure criteria first (more important to catch)
        for criterion in thesis.failure_criteria:
            if self._keyword_match(text, criterion):
                return "contradicting"

        # Check validation criteria
        for criterion in thesis.validation_criteria:
            if self._keyword_match(text, criterion):
                return "supporting"

        return "neutral"

    def score_news_item(
        self,
        headline: str,
        url: str,
        summary: str,
        thesis: Thesis,
    ) -> dict:
        """Score a single news item against a thesis with detailed breakdown.

        Computes keyword overlap + sentiment heuristics for a richer score
        than the simple supporting/neutral/contradicting classification.

        Args:
            headline: Article headline text.
            url: Article URL.
            summary: Article summary/snippet text.
            thesis: Thesis to score against.

        Returns:
            Dict with keys: headline, url, sentiment, score (float 0-1),
            matched_keywords (list), explanation (str).
        """
        article = {"title": headline, "url": url, "summary": summary}
        sentiment = self.score_article(article, thesis)

        text = f"{headline} {summary}".lower()

        # Compute keyword overlap score
        all_keywords = self._extract_keywords(thesis)
        matched = [kw for kw in all_keywords if kw.lower() in text]
        keyword_score = len(matched) / max(len(all_keywords), 1)

        # Sentiment heuristics
        positive_words = {
            "surge", "growth", "increase", "record", "strong",
            "rally", "boost", "gains", "bullish", "accelerate",
        }
        negative_words = {
            "decline", "drop", "fall", "cut", "weak",
            "crash", "loss", "bearish", "slowdown", "plunge",
        }

        pos_count = sum(1 for w in positive_words if w in text)
        neg_count = sum(1 for w in negative_words if w in text)
        sentiment_bias = (pos_count - neg_count) / max(pos_count + neg_count, 1)

        # Combined score: keyword overlap weighted more heavily
        score = keyword_score * 0.7 + (0.5 + sentiment_bias * 0.5) * 0.3
        score = max(0.0, min(1.0, score))

        explanation_parts: list[str] = []
        if matched:
            explanation_parts.append(f"matched keywords: {', '.join(matched)}")
        if sentiment_bias > 0:
            explanation_parts.append("positive sentiment")
        elif sentiment_bias < 0:
            explanation_parts.append("negative sentiment")

        return {
            "headline": headline,
            "url": url,
            "sentiment": sentiment,
            "score": round(score, 3),
            "matched_keywords": matched,
            "explanation": (
                "; ".join(explanation_parts)
                if explanation_parts else "no strong signals"
            ),
        }

    def _extract_keywords(self, thesis: Thesis) -> list[str]:
        """Extract all significant keywords from a thesis's criteria and metadata.

        Args:
            thesis: Thesis model.

        Returns:
            List of keyword strings (length > 3).
        """
        sources = thesis.validation_criteria + thesis.failure_criteria + [thesis.title]
        sources.extend(thesis.symbols)
        words: list[str] = []
        for src in sources:
            words.extend(w for w in src.split() if len(w) > 3)
        return list(set(words))

    def _keyword_match(self, text: str, criterion: str) -> bool:
        """Check if a criterion's keywords appear in text.

        Splits the criterion into words and checks if any significant word
        (length > 3) appears in the text.

        Args:
            text: Lowercased text to search in.
            criterion: Criterion string to extract keywords from.

        Returns:
            True if at least 2 significant keywords match, or 1 if criterion
            has fewer than 2 significant words.
        """
        words = [w.lower() for w in criterion.split() if len(w) > 3]
        if not words:
            return False

        matches = sum(1 for w in words if w in text)
        threshold = min(2, len(words))
        return matches >= threshold

    def score_news_batch(
        self,
        thesis_id: int,
        news_items: list[dict],
    ) -> list[dict]:
        """Score a batch of externally-provided news items against a thesis.

        Use this when news items are provided from an external source (e.g.,
        a web_search tool or RSS feed) rather than fetched internally.

        Args:
            thesis_id: ID of the thesis to score against.
            news_items: List of dicts with keys: headline, url, summary.

        Returns:
            List of scored item dicts. Empty list if thesis not found.
        """
        thesis = self.thesis_engine.get_thesis(thesis_id)
        if not thesis:
            return []

        results: list[dict] = []
        for item in news_items:
            scored = self.score_news_item(
                headline=item.get("headline", ""),
                url=item.get("url", ""),
                summary=item.get("summary", ""),
                thesis=thesis,
            )

            # Store in thesis_news, skip duplicates by URL
            url = item.get("url", "")
            if url:
                existing = self.db.fetchone(
                    "SELECT id FROM thesis_news WHERE thesis_id = ? AND url = ?",
                    (thesis_id, url),
                )
                if existing:
                    continue

            self.db.execute(
                """INSERT INTO thesis_news
                   (thesis_id, headline, url, sentiment)
                   VALUES (?, ?, ?, ?)""",
                (thesis_id, scored["headline"], scored["url"], scored["sentiment"]),
            )
            results.append(scored)

        if results:
            self.db.connect().commit()

        return results

    def validate_thesis(self, thesis_id: int) -> dict:
        """Run full validation cycle for a single thesis.

        Searches news, scores articles, stores results in thesis_news table,
        and triggers auto-transition if evidence thresholds are met.

        Args:
            thesis_id: ID of the thesis to validate.

        Returns:
            Dict with keys: thesis_id, articles_found, supporting, contradicting,
            neutral, transition (str or None).
        """
        thesis = self.thesis_engine.get_thesis(thesis_id)
        if not thesis:
            return {"thesis_id": thesis_id, "error": "not_found"}

        articles = self.search_news(thesis_id)
        supporting = 0
        contradicting = 0
        neutral = 0

        for article in articles:
            sentiment = self.score_article(article, thesis)
            # Store in thesis_news, skip duplicates by URL
            if article.get("url"):
                existing = self.db.fetchone(
                    "SELECT id FROM thesis_news WHERE thesis_id = ? AND url = ?",
                    (thesis_id, article["url"]),
                )
                if existing:
                    continue

            self.db.execute(
                """INSERT INTO thesis_news
                   (thesis_id, headline, url, sentiment)
                   VALUES (?, ?, ?, ?)""",
                (
                    thesis_id,
                    article.get("title", ""),
                    article.get("url", ""),
                    sentiment,
                ),
            )

            if sentiment == "supporting":
                supporting += 1
            elif sentiment == "contradicting":
                contradicting += 1
            else:
                neutral += 1

        if supporting or contradicting or neutral:
            self.db.connect().commit()

        transition = self.auto_transition(thesis_id, supporting, contradicting)

        return {
            "thesis_id": thesis_id,
            "articles_found": len(articles),
            "supporting": supporting,
            "contradicting": contradicting,
            "neutral": neutral,
            "transition": transition,
        }

    def _matched_criteria(self, article: dict, thesis: Thesis, sentiment: str) -> str:
        """Find which criteria an article matched.

        Args:
            article: Article dict.
            thesis: Thesis model.
            sentiment: The scored sentiment.

        Returns:
            The first matching criterion string, or empty string.
        """
        title_lower = article.get("title", "").lower()
        if sentiment == "contradicting":
            criteria = thesis.failure_criteria
        else:
            criteria = thesis.validation_criteria
        for criterion in criteria:
            if self._keyword_match(title_lower, criterion):
                return criterion
        return ""

    def validate_all(self) -> list[dict]:
        """Validate all active theses against news.

        Returns:
            List of validation result dicts from validate_thesis().
        """
        active_statuses = [
            ThesisStatus.ACTIVE.value,
            ThesisStatus.STRENGTHENING.value,
            ThesisStatus.CONFIRMED.value,
            ThesisStatus.WEAKENING.value,
        ]
        placeholders = ",".join("?" for _ in active_statuses)
        rows = self.db.fetchall(
            f"SELECT id FROM theses WHERE status IN ({placeholders})",
            tuple(active_statuses),
        )

        results: list[dict] = []
        for row in rows:
            result = self.validate_thesis(row["id"])
            results.append(result)

        return results

    def check_stale(self, max_age_days: int = 30) -> list[dict]:
        """Find theses that haven't been validated in N days.

        Args:
            max_age_days: Maximum number of days since last news check.
                Defaults to 30.

        Returns:
            List of dicts with thesis_id, title, status, last_news_date.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
        rows = self.db.fetchall(
            """SELECT t.id, t.title, t.status,
                      MAX(tn.timestamp) as last_news
               FROM theses t
               LEFT JOIN thesis_news tn ON t.id = tn.thesis_id
               WHERE t.status IN ('active', 'strengthening', 'confirmed', 'weakening')
               GROUP BY t.id
               HAVING last_news IS NULL OR last_news < ?""",
            (cutoff,),
        )

        return [
            {
                "thesis_id": r["id"],
                "title": r["title"],
                "status": r["status"],
                "last_news_date": r["last_news"],
            }
            for r in rows
        ]

    def auto_transition(self, thesis_id: int, supporting: int, contradicting: int) -> str | None:
        """Automatically transition thesis status based on evidence counts.

        Transition rules:
            - 3+ supporting, 0 contradicting in 7 days → 'strengthening'
            - 3+ contradicting, 0 supporting in 7 days → 'weakening'
            - Already weakening + 2+ more contradicting → 'invalidated'
            - On invalidation: generate SELL signals for all linked positions

        Args:
            thesis_id: ID of the thesis to potentially transition.
            supporting: Count of new supporting articles.
            contradicting: Count of new contradicting articles.

        Returns:
            New status string if transition occurred, None otherwise.
        """
        thesis = self.thesis_engine.get_thesis(thesis_id)
        if not thesis:
            return None

        # Also check recent 7-day totals from DB
        week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        recent = self.db.fetchone(
            """SELECT
                   SUM(CASE WHEN sentiment = 'supporting' THEN 1 ELSE 0 END) as sup,
                   SUM(CASE WHEN sentiment = 'contradicting' THEN 1 ELSE 0 END) as con
               FROM thesis_news
               WHERE thesis_id = ? AND timestamp >= ?""",
            (thesis_id, week_ago),
        )

        total_sup = (recent["sup"] or 0) if recent else supporting
        total_con = (recent["con"] or 0) if recent else contradicting

        new_status: ThesisStatus | None = None

        if thesis.status == ThesisStatus.WEAKENING and total_con >= 2:
            new_status = ThesisStatus.INVALIDATED
        elif total_con >= 3 and total_sup == 0:
            new_status = ThesisStatus.WEAKENING
        elif total_sup >= 3 and total_con == 0:
            new_status = ThesisStatus.STRENGTHENING

        if not new_status:
            return None

        # Skip if already in target status
        if thesis.status == new_status:
            return None

        try:
            self.thesis_engine.transition_status(
                thesis_id,
                new_status,
                reason=f"Auto-transition: {total_sup} supporting, {total_con} contradicting in 7d",
                evidence=f"News validation: sup={total_sup}, con={total_con}",
            )
        except ValueError:
            logger.warning(
                "Invalid transition %s -> %s for thesis %d",
                thesis.status,
                new_status,
                thesis_id,
            )
            return None

        # On invalidation, generate SELL signals for linked positions
        if new_status == ThesisStatus.INVALIDATED:
            self._generate_sell_signals(thesis_id)

        return new_status.value

    def _generate_sell_signals(self, thesis_id: int) -> None:
        """Generate SELL signals for all positions linked to an invalidated thesis.

        Args:
            thesis_id: ID of the invalidated thesis.
        """
        positions = self.db.fetchall("SELECT * FROM positions WHERE thesis_id = ?", (thesis_id,))
        for pos in positions:
            signal = Signal(
                action=SignalAction.SELL,
                symbol=pos["symbol"],
                thesis_id=thesis_id,
                confidence=0.7,
                source=SignalSource.NEWS_EVENT,
                reasoning=f"Thesis invalidated by news evidence. Sell {pos['symbol']}.",
                status=SignalStatus.PENDING,
            )
            self.signal_engine.create_signal(signal)
            logger.info(
                "Generated SELL signal for %s due to thesis %d invalidation",
                pos["symbol"],
                thesis_id,
            )
