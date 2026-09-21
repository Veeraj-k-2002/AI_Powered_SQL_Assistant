"""
app/services/sql_chat_service.py
─────────────────────────────────
The brain of the chatbot.

Flow per user message
─────────────────────
  1. Load the connected database config from our app DB
  2. Connect to the user's PostgreSQL and inspect its schema
  3. Retrieve the last N chat messages for context
  4. Ask Groq/Llama3 to produce a safe SELECT query (LangChain chain)
  5. Validate the query (must start with SELECT, no DDL/DML)
  6. Execute the query against the user's database
  7. Ask Groq/Llama3 to summarise the result in plain English
  8. Ask Groq/Llama3 to decide the best chart for the data
  9. Persist and return the full ChatMessage
"""

import re
import json
import math
import asyncio
import hashlib
from decimal import Decimal
from datetime import date, datetime, time
from textwrap import dedent
from uuid import UUID
from collections import Counter
import pandas as pd
from sqlalchemy import text, inspect, URL
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.future import select
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_groq import ChatGroq
from sqlglot import exp, parse

from app.core.config import settings, AppException
from app.core.crypto import decrypt_secret
from app.core.logger import get_logger
from app.models.models import ConnectedDatabase, ChatMessage
from app.schemas.chat_schema import ChartConfig, ChartData
from app.services.query_cache import QueryResultCache

logger = get_logger(__name__)
query_cache = QueryResultCache(
    ttl_seconds=settings.QUERY_CACHE_TTL_SECONDS,
    max_entries=settings.QUERY_CACHE_MAX_ENTRIES,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_json_safe(obj):
    """Recursively convert non-JSON-serialisable types (UUID, Decimal, datetime)."""
    if isinstance(obj, UUID):
        return str(obj)
    # if isinstance(obj, datetime):
    #     return obj.isoformat()
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, list):
        return [_make_json_safe(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    return obj


# ── Async wrapper around user's PostgreSQL ────────────────────────────────────

class _UserDatabase:
    """Wraps an async SQLAlchemy engine pointed at the user's PostgreSQL."""

    def __init__(self, engine, schema: str):
        self.engine = engine
        self.schema = schema

    async def get_schema_info(self) -> str:
        """Return a human-readable schema string (table + column list)."""
        async with self.engine.connect() as conn:
            def _inspect(sync_conn):
                insp = inspect(sync_conn)
                tables = insp.get_table_names(schema=self.schema)
                if not tables:
                    return f"No tables found in schema '{self.schema}'."
                parts = []
                for tbl in tables:
                    cols = insp.get_columns(tbl, schema=self.schema)
                    col_defs = ", ".join(
                        f"{c['name']} ({c['type']})"
                        for c in cols
                    )

                    fks = insp.get_foreign_keys(tbl, schema=self.schema)

                    fk_defs = []
                    for fk in fks:
                        referred_table = fk["referred_table"]
                        referred_schema = fk["referred_schema"] or self.schema
                        local_cols = ", ".join(fk["constrained_columns"])
                        remote_cols = ", ".join(fk["referred_columns"])

                        fk_defs.append(
                            f"{self.schema}.{tbl}({local_cols}) -> "
                            f"{referred_schema}.{referred_table}({remote_cols})"
                        )

                    table_info = f"Table `{self.schema}.{tbl}`: {col_defs}"

                    if fk_defs:
                        table_info += "\n  Foreign Keys:\n    " + "\n    ".join(fk_defs)

                    parts.append(table_info)
                return "\n".join(parts)

            return await conn.run_sync(_inspect)

    async def run_query(self, sql: str) -> pd.DataFrame:
        """Execute a bounded SELECT in a database-enforced read-only transaction."""
        async with self.engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                # The setting is validated at application startup, so interpolation
                # cannot be influenced by SQL supplied by a user or model.
                await conn.execute(text(f"SET LOCAL statement_timeout = {settings.QUERY_TIMEOUT_MS}"))
                rows = (await conn.execute(text(sql))).mappings().all()
        return pd.DataFrame(rows)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def list_tables(self) -> list[str]:
        async with self.engine.connect() as conn:
            def _tables(sync_conn):
                return inspect(sync_conn).get_table_names(schema=self.schema)
            return await conn.run_sync(_tables)


# ── Main service ──────────────────────────────────────────────────────────────

class SqlChatService:
    def __init__(self, db: AsyncSession):
        self.db = db
        # Groq is free-tier; use llama3-70b for best quality
        self.llm = ChatGroq(
            # model="llama3-70b-8192",
            # model="llama-3.1-8b-instant",
            model="openai/gpt-oss-20b",
            groq_api_key=settings.GROQ_API_KEY,
            temperature=0,
        )

    # ── Database connection ──────────────────────────────────────────────────

    async def _get_user_db(self, database_id: UUID) -> tuple[_UserDatabase, ConnectedDatabase]:
        """Fetch config from app DB and return a live connection to the user's DB."""
        result = (
            await self.db.execute(
                select(ConnectedDatabase).where(ConnectedDatabase.id == database_id)
            )
        ).scalar_one_or_none()

        if not result:
            raise AppException(404, "Database connection not found.")

        cfg = result
        try:
            password = decrypt_secret(cfg.encrypted_password)
        except ValueError as exc:
            raise AppException(500, "Saved database credentials are unavailable. Update the connection.") from exc
        uri = URL.create(
            "postgresql+asyncpg",
            username=cfg.username,
            password=password,
            host=cfg.host,
            port=int(cfg.port),
            database=cfg.database_name,
        )
        engine = create_async_engine(uri, pool_pre_ping=True)

        # Verify the connection works before proceeding
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            await engine.dispose()
            logger.warning("Database connection failed for id=%s (%s)", database_id, type(exc).__name__)
            raise AppException(503, "Cannot connect to the selected database. Check its saved connection details.") from exc

        return _UserDatabase(engine, cfg.schema_name), cfg



    @staticmethod
    def _rule_based_chart(
        user_query: str,
        df: pd.DataFrame,
    ) -> ChartConfig | None:
        """
        Deterministic chart selection for obvious analytical intents.

        Returns:
            ChartConfig if a strong chart choice can be determined.
            None if the LLM should decide.
        """

        if df.empty or len(df.columns) < 2:
            return ChartConfig()

        question = user_query.lower().strip()

        columns = df.columns.tolist()

        # ---------------------------------------------------------
        # Identify categorical / numeric columns
        # ---------------------------------------------------------

        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        categorical_cols = [
            c for c in columns
            if c not in numeric_cols
        ]

        # Ignore columns with very high cardinality for pie charts
        pie_category = None
        pie_value = None

        for col in categorical_cols:
            unique_count = df[col].nunique(dropna=True)

            if 2 <= unique_count <= 8 and numeric_cols:
                pie_category = col
                break

        if pie_category and numeric_cols:
            # Prefer a count/amount/total-like numeric column
            preferred_value_names = [
                "count",
                "total",
                "amount",
                "sales",
                "revenue",
                "quantity",
                "value",
                "percentage",
                "percent",
            ]

            for preferred in preferred_value_names:
                for col in numeric_cols:
                    if preferred in col.lower():
                        pie_value = col
                        break

                if pie_value:
                    break

            if not pie_value:
                pie_value = numeric_cols[0]

        # ---------------------------------------------------------
        # PIE CHART INTENT
        # ---------------------------------------------------------

        pie_keywords = [
            "percentage",
            "percent",
            "%",
            "proportion",
            "share",
            "distribution",
            "breakdown",
            "composition",
            "split",
            "portion",
            "contribution",
        ]

        asks_for_pie = any(
            keyword in question
            for keyword in pie_keywords
        )

        if asks_for_pie and pie_category and pie_value:
            return ChartConfig(
                chart_type="pie",
                x_axis=pie_category,
                y_axis=pie_value,
                reason="Pie chart is suitable for showing the distribution or proportion across categories.",
                data=ChartData(
                    x=_make_json_safe(df[pie_category].tolist()),
                    y=_make_json_safe(df[pie_value].tolist()),
                ),
            )

        # ---------------------------------------------------------
        # DATE/TIME → LINE
        # ---------------------------------------------------------

        datetime_cols = df.select_dtypes(
            include=["datetime", "datetimetz"]
        ).columns.tolist()

        if datetime_cols and numeric_cols:
            return ChartConfig(
                chart_type="line",
                x_axis=datetime_cols[0],
                y_axis=numeric_cols[0],
                reason="A line chart is suitable for showing changes over time.",
                data=ChartData(
                    x=_make_json_safe(df[datetime_cols[0]].tolist()),
                    y=_make_json_safe(df[numeric_cols[0]].tolist()),
                ),
            )

        # Also detect date-looking column names
        date_name_keywords = [
            "date",
            "month",
            "year",
            "time",
            "day",
            "week",
        ]

        date_like_cols = [
            c for c in columns
            if any(
                keyword in c.lower()
                for keyword in date_name_keywords
            )
        ]

        if date_like_cols and numeric_cols:
            return ChartConfig(
                chart_type="line",
                x_axis=date_like_cols[0],
                y_axis=numeric_cols[0],
                reason="A line chart is suitable for showing a trend over time.",
                data=ChartData(
                    x=_make_json_safe(df[date_like_cols[0]].tolist()),
                    y=_make_json_safe(df[numeric_cols[0]].tolist()),
                ),
            )

        # ---------------------------------------------------------
        # SCATTER → TWO NUMERIC VARIABLES
        # ---------------------------------------------------------

        if len(numeric_cols) >= 2:
            relationship_keywords = [
                "relationship",
                "correlation",
                "vs",
                "versus",
                "compared",
                "against",
            ]

            if any(keyword in question for keyword in relationship_keywords):
                return ChartConfig(
                    chart_type="scatter",
                    x_axis=numeric_cols[0],
                    y_axis=numeric_cols[1],
                    reason="A scatter plot is suitable for showing the relationship between two numeric variables.",
                    data=ChartData(
                        x=_make_json_safe(df[numeric_cols[0]].tolist()),
                        y=_make_json_safe(df[numeric_cols[1]].tolist()),
                    ),
                )

        # ---------------------------------------------------------
        # BAR → CATEGORY + NUMERIC
        # ---------------------------------------------------------

        if categorical_cols and numeric_cols:
            return ChartConfig(
                chart_type="bar",
                x_axis=categorical_cols[0],
                y_axis=numeric_cols[0],
                reason="A bar chart is suitable for comparing values across categories.",
                data=ChartData(
                    x=_make_json_safe(df[categorical_cols[0]].tolist()),
                    y=_make_json_safe(df[numeric_cols[0]].tolist()),
                ),
            )

        return None



    async def _pick_chart(
        self,
        user_query: str,
        df: pd.DataFrame
    ) -> ChartConfig:

        if df.empty or len(df.columns) < 2:
            return ChartConfig()

        # ---------------------------------------------------------
        # 1. Deterministic rules first
        # ---------------------------------------------------------

        rule_chart = self._rule_based_chart(
            user_query,
            df,
        )

        if rule_chart is not None:
            logger.info(
                "Chart selected using deterministic rules: %s",
                rule_chart.chart_type,
            )
            return rule_chart

        # ---------------------------------------------------------
        # 2. LLM fallback
        # ---------------------------------------------------------

        template = dedent("""
            You are an expert data visualization assistant.

            Choose the MOST APPROPRIATE chart for the user's question
            and the provided query result.

            IMPORTANT CHART RULES:

            PIE CHART:
            Use PIE when:
            - The question asks for percentage, proportion, share,
            distribution, composition, breakdown, or contribution.
            - The data contains ONE categorical column and ONE numeric
            value column.
            - The categorical column has a small number of categories,
            preferably between 2 and 8.
            - Pie charts represent parts of a whole.

            BAR CHART:
            Use BAR when:
            - Comparing values across categories.
            - Ranking products, customers, cities, departments, etc.
            - Showing top/bottom N results.

            LINE CHART:
            Use LINE when:
            - Showing trends over time.
            - Data contains dates, months, years, or timestamps.

            SCATTER CHART:
            Use SCATTER when:
            - Showing the relationship between two numeric variables.
            - The question asks about correlation or relationship.

            NONE:
            Use NONE when a chart would not meaningfully help.

            IMPORTANT:
            - Prefer PIE over BAR when the user explicitly asks for
            percentage, proportion, share, distribution, composition,
            or breakdown.
            - Do not choose a chart merely because it is available.
            - Choose the chart based on both the user's intent and data shape.
            - Use only actual column names.
            - Never invent columns.

            Respond ONLY with valid JSON.

            Columns:
            {columns}

            Data types:
            {dtypes}

            Number of rows:
            {row_count}

            Unique values per column:
            {unique_counts}

            Sample rows:
            {sample}

            User question:
            {question}

            JSON format:
            {{
                "show_chart": true,
                "chart_type": "bar" | "line" | "scatter" | "pie" | "none",
                "x_axis": "column_name" | null,
                "y_axis": "column_name" | null,
                "reason": "one-line explanation"
            }}
        """)

        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | self.llm | StrOutputParser()

        try:
            raw = await chain.ainvoke({
                "columns": df.columns.tolist(),
                "dtypes": {
                    col: str(dtype)
                    for col, dtype in df.dtypes.items()
                },
                "row_count": len(df),
                "unique_counts": {
                    col: int(df[col].nunique(dropna=True))
                    for col in df.columns
                },
                "sample": df.head(5).to_dict(
                    orient="records"
                ),
                "question": user_query,
            })

            raw = re.sub(
                r"```json|```",
                "",
                raw
            ).strip()

            parsed = json.loads(raw)

        except Exception as exc:
            logger.warning(
                "Chart selection failed: %s",
                type(exc).__name__,
            )
            return ChartConfig()

        if not parsed.get("show_chart"):
            return ChartConfig()

        chart_type = parsed.get("chart_type", "none")

        if chart_type == "none":
            return ChartConfig()

        x_col = parsed.get("x_axis")
        y_col = parsed.get("y_axis")

        if x_col not in df.columns or y_col not in df.columns:
            logger.warning(
                "LLM selected invalid chart columns: x=%s y=%s",
                x_col,
                y_col,
            )
            return ChartConfig()

        return ChartConfig(
            chart_type=chart_type,
            x_axis=x_col,
            y_axis=y_col,
            reason=parsed.get("reason", ""),
            data=ChartData(
                x=_make_json_safe(df[x_col].tolist()),
                y=_make_json_safe(df[y_col].tolist()),
            ),
        )



    # ── Chat history (context) ───────────────────────────────────────────────

    async def _get_recent_history(self, database_id: UUID, limit: int = 4) -> str:
        rows = (
            await self.db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.database_id == database_id,
                    ChatMessage.status == "success",
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

        if not rows:
            return "No previous conversation."

        lines = []
        for r in reversed(rows):
            lines.append(f"User: {r.user_query}")
            lines.append(f"SQL:  {r.sql_query}")
        return "\n".join(lines)

    # ── LangChain chains ─────────────────────────────────────────────────────

    async def _generate_sql(
        self, user_query: str, chat_history: str, schema: str, schema_name: str
    ) -> str:
        """Chain 1 – convert natural language to a PostgreSQL SELECT statement."""

        template = dedent("""
            You are an expert PostgreSQL SQL generation assistant.

            Your task is to translate the user's natural-language question into exactly
            one safe, read-only PostgreSQL SELECT statement using ONLY the database
            schema provided below.

            GENERAL RULES:

            - Output ONLY the raw SQL.
            - Do not output markdown, backticks, explanations, or commentary.
            - Generate exactly one SELECT statement.
            - Use only tables, columns, schemas, and relationships present in the
            provided database schema.
            - Never invent tables, columns, relationships, or values.
            - Use the provided foreign-key relationships when constructing JOINs.
            - Respect the provided schema name.
            - Fully qualify table references when appropriate.
            - Use table aliases when helpful and qualify ambiguous columns.
            - If the question cannot be answered using the provided schema and a
            SELECT query, return UNSUPPORTED_QUERY.


            NATURAL-LANGUAGE UNDERSTANDING:

            - Interpret conversational, informal, abbreviated, and grammatically
            imperfect user questions.
            - Do not require the user to use exact database terminology.
            - Understand common natural-language synonyms based on the schema context.
            - Map user intent to the appropriate tables and columns.

            Examples of general language variations:

            - "bought", "purchased", "ordered", "got" may refer to acquisition or
            purchase-related records depending on the schema.
            - "spent", "paid", "cost", "amount" may refer to monetary columns depending
            on the schema.
            - "how many", "number of", "count of" generally imply COUNT.
            - "total", "overall", "combined" generally imply SUM when an additive
            numeric field exists.
            - "average", "mean" generally imply AVG.
            - "highest", "largest", "most expensive", "maximum" generally imply MAX
            or appropriate ORDER BY.
            - "lowest", "smallest", "cheapest", "minimum" generally imply MIN or
            appropriate ORDER BY.
            - "top N" generally implies ORDER BY with LIMIT N.
            - "per", "by", "for each" generally imply GROUP BY when appropriate.


            ENTITY / VALUE RESOLUTION:

            - Users may refer to database entities using partial names, shortened names,
            aliases, or natural-language descriptions.
            - Do not assume that a user-provided partial value is the complete database
            value.
            - When appropriate, use case-insensitive matching such as ILIKE to resolve
            partial text values.
            - Prefer exact matching when the user's value clearly corresponds to an
            exact database value.
            - If a partial value could match multiple records, do not silently assume
            that the records represent the same entity.
            - Use the schema and question context to determine the appropriate entity
            and matching strategy.
            - Never invent a value that is not supported by the user's question or
            database schema.


            MULTI-TABLE REASONING:

            - Determine which tables are required to answer the question.
            - Follow foreign-key relationships to connect related tables.
            - Use the minimum necessary tables.
            - For multi-hop questions, construct the required JOIN chain through the
            available relationships.
            - Do not join tables merely because they exist; join them only when needed
            to answer the question.
            - Avoid Cartesian products unless explicitly required by the question.


            FILTERING:

            - Translate natural-language conditions into appropriate WHERE/HAVING
            clauses.
            - Handle equality, ranges, dates, NULL values, boolean conditions, and
            multiple filters appropriately.
            - Use case-insensitive matching for text when appropriate.
            - Do not apply filters that were not requested by the user.


            AGGREGATION:

            - Translate aggregation intent according to the question and available
            schema.
            - Use COUNT, SUM, AVG, MIN, MAX, GROUP BY, and HAVING when appropriate.
            - Distinguish between row-level values and aggregated values.
            - When calculating totals involving quantities and prices, use the
            appropriate columns and relationships from the schema.
            - Do not assume the existence of a particular business metric or column.
            - If the meaning of an aggregation is genuinely ambiguous, choose the
            interpretation best supported by the question and schema.


            SORTING AND LIMITING:

            - Interpret natural-language ranking such as:
            "highest", "lowest", "top", "bottom", "most", "least", "latest",
            and "earliest".
            - Use ORDER BY and LIMIT appropriately.
            - Preserve deterministic ordering when practical.


            DATES AND TIME:

            - Interpret common date/time expressions such as:
            today, yesterday, this month, last month, this year, latest, oldest,
            recent, before, after, and between.
            - Use date/time columns available in the schema.
            - Do not invent date columns.


            DUPLICATES:

            - Use DISTINCT only when the user's intent requires unique results.
            - Do not remove duplicates automatically when duplicate rows represent
            meaningful transactions, events, or quantities.


            NULL HANDLING:

            - Correctly handle NULL values using IS NULL / IS NOT NULL.
            - Do not use = NULL or != NULL.


            SAFETY:

            - Never generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE,
            GRANT, REVOKE, MERGE, EXECUTE, or any other write/DDL operation.
            - Generate exactly one SELECT statement.
            - Do not access system schemas such as pg_catalog, information_schema,
            or pg_toast unless explicitly allowed by the application.
            - Do not use dangerous database functions.
            - Do not execute stored procedures or commands.
            - If the request requires modifying data or schema, return
            UNSUPPORTED_QUERY.


            SCHEMA NAME:
            {schema_name}

            DATABASE SCHEMA:
            {schema}

            RECENT CONVERSATION:
            {chat_history}

            USER QUESTION:
            {question}

            SQL:
        """)

        prompt = ChatPromptTemplate.from_template(template)

        chain = (
            RunnablePassthrough.assign(
                schema=lambda _: schema,
                schema_name=lambda _: schema_name,
                chat_history=lambda _: chat_history,
            )
            | prompt
            | self.llm
            | StrOutputParser()
        )

        try:
            return await chain.ainvoke({"question": user_query})
        except Exception as exc:
            err = str(exc)
            if "rate_limit" in err.lower():
                raise AppException(429, "LLM rate limit reached. Try again shortly.")
            logger.error("SQL generation failed (%s)", type(exc).__name__)
            raise AppException(
                502,
                "The SQL generation service is temporarily unavailable."
            ) from exc

    async def _summarise_result(
        self, user_query: str, schema: str, df: pd.DataFrame
    ) -> str:
        """Chain 2 – convert the DataFrame into a plain-English bullet summary."""

        numeric_cols = df.select_dtypes(include="number").columns.tolist()

        stats_lines = []
        for col in numeric_cols:
            stats_lines.append(
                f"  {col}: mean={df[col].mean():.2f}, "
                f"min={df[col].min():.2f}, max={df[col].max():.2f}"
            )

        if len(numeric_cols) >= 2:
            corr = df[numeric_cols].corr()
            for i, c1 in enumerate(numeric_cols):
                for c2 in numeric_cols[i+1:]:
                    stats_lines.append(
                        f"  correlation({c1}, {c2}) = {corr.loc[c1, c2]:.4f}"
                    )

        precomputed_stats = "\n".join(stats_lines) if stats_lines else "No numeric columns."

        template = dedent("""
            You are a data analyst. Summarise the query result concisely.
            - Use 1-3 bullet points.
            - USE ONLY the pre-computed statistics provided below — do NOT recalculate anything yourself.
            - Cover ALL columns mentioned in the statistics, not just one.
            - IMPORTANT: Do NOT use the dollar sign ($) anywhere in your response under any circumstances.
            - Keep it factual — no waffle.

            Schema: {schema}
            User question: {question}
            Query result (first 50 rows): {result}

            Pre-computed statistics (use these exactly, do not recalculate):
            {stats}
        """)

        prompt = ChatPromptTemplate.from_template(template)
        chain = (
            RunnablePassthrough.assign(schema=lambda _: schema)
            | prompt
            | self.llm
            | StrOutputParser()
        )

        try:
            summary = await chain.ainvoke({
                "question": user_query,
                "result": df.head(50).to_string(index=False),
                "stats": precomputed_stats,
            })

            return summary

        except Exception as exc:
            logger.warning("Summary generation failed (%s)", type(exc).__name__)
            return "Results retrieved. See the table below."

    # async def _pick_chart(self, user_query: str, df: pd.DataFrame) -> ChartConfig:
    #     """Chain 3 – choose the best Plotly chart type for the data."""

    #     if df.empty or len(df.columns) < 2:
    #         return ChartConfig()

    #     template = dedent("""
    #         Decide whether a chart would help visualise this data.
    #         Respond ONLY with a JSON object — no markdown, no commentary.

    #         Columns: {columns}
    #         Sample rows (first 5): {sample}
    #         User question: {question}

    #         JSON format:
    #         {{
    #             "show_chart": true | false,
    #             "chart_type": "bar" | "line" | "scatter" | "pie" | "none",
    #             "x_axis": "column_name" | null,
    #             "y_axis": "column_name" | null,
    #             "reason": "one-line explanation"
    #         }}
    #     """)

    #     prompt = ChatPromptTemplate.from_template(template)
    #     chain = prompt | self.llm | StrOutputParser()

    #     raw = await chain.ainvoke({
    #         "columns": df.columns.tolist(),
    #         "sample": df.head(5).to_dict(orient="records"),
    #         "question": user_query,
    #     })

    #     # Strip ```json fences if present
    #     raw = re.sub(r"```json|```", "", raw).strip()

    #     try:
    #         parsed = json.loads(raw)
    #     except json.JSONDecodeError:
    #         logger.warning(f"Could not parse chart JSON: {raw}")
    #         return ChartConfig()

    #     if not parsed.get("show_chart"):
    #         return ChartConfig()

    #     x_col = parsed.get("x_axis")
    #     y_col = parsed.get("y_axis")
    #     x_vals, y_vals = [], []

    #     if x_col in df.columns and y_col in df.columns:
    #         x_vals = _make_json_safe(df[x_col].tolist())
    #         y_vals = _make_json_safe(df[y_col].tolist())

    #     return ChartConfig(
    #         chart_type=parsed.get("chart_type", "none"),
    #         x_axis=x_col,
    #         y_axis=y_col,
    #         reason=parsed.get("reason", ""),
    #         data=ChartData(x=x_vals, y=y_vals),
    #     )

    # ── SQL validation ───────────────────────────────────────────────────────

    @staticmethod
    def _validate_sql(
        sql: str,
        allowed_schema: str,
        allowed_tables: set[str],
    ) -> str:
        """Parse and enforce one bounded, schema-safe, read-only PostgreSQL query."""
        sql = re.sub(r"```sql|```", "", sql).strip()
        sql = re.sub(r"(?i)^\[?SQL:\s*", "", sql).strip()

        if sql == "UNSUPPORTED_QUERY":
            raise AppException(400, "This question cannot be answered with a SELECT query.")

        try:
            statements = parse(sql, read="postgres")
        except Exception as exc:
            raise AppException(400, "The generated SQL could not be parsed safely.") from exc

        if len(statements) != 1 or statements[0] is None:
            raise AppException(400, "Exactly one read-only SELECT statement is allowed.")
        statement = statements[0]
        if not isinstance(statement, exp.Select) or statement.args.get("into"):
            raise AppException(400, "Only a SELECT query is allowed.")

        forbidden_nodes = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.AlterTable, exp.Command)
        if any(statement.find(node) for node in forbidden_nodes):
            raise AppException(400, "The query contains a disallowed operation.")

        blocked_schemas = {"pg_catalog", "information_schema", "pg_toast"}
        # for table in statement.find_all(exp.Table):
        #     if (table.db or "").lower() in blocked_schemas or (table.catalog or "").lower() in blocked_schemas:
        #         raise AppException(400, "System schemas cannot be queried.")

        for table in statement.find_all(exp.Table):
            table_name = table.name
            table_schema = table.db

            if table_schema and table_schema != allowed_schema:
                raise AppException(
                    400,
                    "The query references a schema that is not allowed."
                )

            if table_name not in allowed_tables:
                raise AppException(
                    400,
                    f"The query references an unknown table: {table_name}."
                )

        dangerous_functions = {
            "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
            "pg_stat_file", "dblink", "dblink_connect", "lo_import", "lo_export",
            "set_config", "current_setting",
        }
        for function in statement.find_all(exp.Func):
            if function.sql_name().lower() in dangerous_functions:
                raise AppException(400, "The query uses a disallowed database function.")

        # Enforce a server-defined cap even if the model omitted a LIMIT or chose
        # a larger one. sqlglot serialises an AST, never model-provided raw SQL.
        limit = statement.args.get("limit")
        current_limit = None
        if limit and isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
            current_limit = int(limit.expression.this)
        if current_limit is None or current_limit > settings.MAX_QUERY_ROWS:
            statement = statement.limit(settings.MAX_QUERY_ROWS)
        return statement.sql(dialect="postgres")

    # ── Public entry point ───────────────────────────────────────────────────

    async def chat(self, database_id: UUID, user_query: str) -> ChatMessage:
        """Full pipeline: NL → SQL → execute → summarise → chart → persist."""

        new_msg = ChatMessage(
            database_id=database_id,
            user_query=user_query,
            status="error",
        )

        user_db = None
        try:
            # 1. Connect to the user's database
            user_db, _cfg = await self._get_user_db(database_id)
            schema = await user_db.get_schema_info()
            schema_name = _cfg.schema_name
            logger.info(f"Schema loaded for database '{_cfg.name}'")

            # 2. Retrieve recent history for context
            chat_history = await self._get_recent_history(database_id)

            # 3. Generate SQL
            raw_sql = await self._generate_sql(user_query, chat_history, schema, schema_name)
            logger.info(f"Generated SQL: {raw_sql[:120]}")

            # 4. Validate (SELECT-only)
            tables = set(await user_db.list_tables())

            clean_sql = self._validate_sql(
                raw_sql,
                schema_name,
                tables,
            )
            new_msg.sql_query = clean_sql

            # 5. Execute query with a process-wide, schema-aware TTL cache.
            schema_hash = hashlib.sha256(schema.encode()).hexdigest()
            cache_key = f"{database_id}:{schema_hash}:{clean_sql}"
            df = await query_cache.get(cache_key)
            if df is not None:
                logger.debug("Query cache hit for database id=%s", database_id)
            else:
                df = await user_db.run_query(clean_sql)
                await query_cache.set(cache_key, df)
                logger.info("Query returned %s rows.", len(df))

            # Serialise table for storage
            for col in df.select_dtypes(include=["object"]).columns:
                df[col] = df[col].where(df[col].notna(), None)

            table_records = _make_json_safe(df.head(500).to_dict(orient="records"))
            new_msg.result_table = table_records

            # 6. Summarise
            summary = await self._summarise_result(user_query, schema, df)
            new_msg.result_summary = summary

            # 7. Pick chart
            chart = await self._pick_chart(user_query, df)
            new_msg.chart_config = _make_json_safe(chart.model_dump())

            new_msg.status = "success"

        except AppException as exc:
            # AppException details are deliberately user-safe; persist them so a
            # history view explains why a rejected request did not run.
            new_msg.error_message = exc.detail
            raise
        except Exception as exc:
            logger.exception("Unexpected chat pipeline failure (%s)", type(exc).__name__)
            new_msg.error_message = "The query could not be completed safely. Please try again."
            new_msg.status = "error"

        finally:
            if user_db is not None:
                await user_db.dispose()
            self.db.add(new_msg)
            await self.db.commit()
            await self.db.refresh(new_msg)

        if new_msg.status == "error" and new_msg.error_message:
            raise AppException(500, new_msg.error_message)

        return new_msg
