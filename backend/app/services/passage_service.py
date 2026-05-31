"""古籍文章服务。"""

from dataclasses import dataclass
import hashlib
import io
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.execution import ExecutionRun, ExecutionStepRun
from app.models.observability import ExecutionTraceSummary, LLMCallLog
from app.models.passage import Passage
from app.models.user import User

# markitdown 支持的全部上传后缀（与路由层白名单保持一致）。
MARKITDOWN_SUPPORTED_SUFFIXES = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".docx",
        ".doc",
        ".pdf",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".csv",
        ".html",
        ".htm",
        ".json",
        ".xml",
        ".epub",
    }
)


@dataclass(frozen=True, slots=True)
class PassageUsageOverviewRow:
    """最新一次 passage run 的资源用量摘要。"""

    doc_id: int
    title: str
    workflow_status: str
    llm_call_count: int
    total_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int
    cache_hit_ratio: float
    estimated_total_cost: float
    currency: str


class PassageService:
    """处理古籍上传、录入与任务查询。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def normalize_context_for_hash(context: str) -> str:
        """Normalize harmless formatting differences before duplicate checks."""

        text = context.replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line.rstrip() for line in text.split("\n")).strip()

    @classmethod
    def compute_content_hash(cls, context: str) -> str:
        """Return the stable duplicate key for a passage body."""

        normalized = cls.normalize_context_for_hash(context)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def find_by_content_hash(self, content_hash: str) -> Passage | None:
        """Find an already stored passage with the same normalized body."""

        return self.db.query(Passage).filter(Passage.content_hash == content_hash).first()

    def find_duplicate_passage(self, context: str, content_hash: str | None = None) -> Passage | None:
        """Find a duplicate passage, including legacy rows created before hashing."""

        resolved_content_hash = content_hash or self.compute_content_hash(context)
        existing = self.find_by_content_hash(resolved_content_hash)
        if existing is not None:
            return existing

        normalized_context = self.normalize_context_for_hash(context)
        legacy_candidates = self.db.query(Passage).filter(Passage.content_hash.is_(None)).all()
        for candidate in legacy_candidates:
            if self.normalize_context_for_hash(candidate.context) == normalized_context:
                candidate.content_hash = resolved_content_hash
                self.db.add(candidate)
                self.db.commit()
                self.db.refresh(candidate)
                return candidate
        return None

    def create_passage(
        self,
        user: User,
        title: str,
        context: str,
        source_type: str,
        file_name: str | None = None,
        content_hash: str | None = None,
        commit: bool = True,
    ) -> Passage:
        """创建 Passage 数据。"""

        resolved_content_hash = content_hash or self.compute_content_hash(context)
        passage = Passage(
            title=title,
            context=context,
            source_type=source_type,
            file_name=file_name,
            content_hash=resolved_content_hash,
            created_by=user.id,
            workflow_status="pending",
        )
        self.db.add(passage)
        if commit:
            self.db.commit()
            self.db.refresh(passage)
        else:
            self.db.flush()
        return passage

    def list_passages(self) -> list[Passage]:
        """列出全部文章。"""

        return self.db.query(Passage).order_by(Passage.updated_at.desc()).all()

    def get_passage(self, doc_id: int) -> Passage:
        """根据 doc_id 获取文章。"""

        passage = self.db.query(Passage).filter(Passage.doc_id == doc_id).first()
        if passage is None:
            raise ValueError("文章不存在。")
        return passage

    def list_passage_runs(self, doc_id: int) -> list[tuple[ExecutionRun, list[ExecutionStepRun]]]:
        """获取 Passage 关联的执行记录与步骤。"""

        runs = (
            self.db.query(ExecutionRun)
            .filter(ExecutionRun.passage_id == doc_id)
            .order_by(ExecutionRun.started_at.desc())
            .all()
        )

        result: list[tuple[ExecutionRun, list[ExecutionStepRun]]] = []
        for run in runs:
            steps = (
                self.db.query(ExecutionStepRun)
                .filter(ExecutionStepRun.execution_run_id == run.id)
                .order_by(ExecutionStepRun.step_no.asc())
                .all()
            )
            result.append((run, steps))

        return result

    def get_latest_passage_trace(
        self,
        doc_id: int,
    ) -> tuple[int | None, ExecutionTraceSummary | None, list[LLMCallLog]]:
        """返回指定文章最新一次执行的 trace 汇总与 LLM 调用。

        最新执行以 execution_runs 为准，而不是以 llm_call_logs 为准。这样当
        rerun 刚开始或没有产生 LLM 调用时，前端不会继续展示上一轮的 token 数据。
        """

        latest_run = (
            self.db.query(ExecutionRun)
            .filter(ExecutionRun.passage_id == doc_id)
            .order_by(ExecutionRun.started_at.desc(), ExecutionRun.id.desc())
            .first()
        )
        if latest_run is None:
            return None, None, []

        summary = (
            self.db.query(ExecutionTraceSummary)
            .filter(ExecutionTraceSummary.execution_run_id == latest_run.id)
            .first()
        )
        calls = (
            self.db.query(LLMCallLog)
            .filter(LLMCallLog.execution_run_id == latest_run.id)
            .filter(LLMCallLog.passage_id == doc_id)
            .order_by(LLMCallLog.id.asc())
            .all()
        )
        return latest_run.id, summary, calls

    def get_passage_usage_overview_rows(self) -> list[PassageUsageOverviewRow]:
        """按文章返回最新一次执行的资源用量。

        只读取每篇文章最新 execution_run 关联的 summary；如果最新 run 尚无
        summary，则该文章显示 0 用量，避免把旧 run 的成本误算进当前状态。
        """

        passages = self.db.query(Passage).order_by(Passage.doc_id.desc()).all()
        rows: list[PassageUsageOverviewRow] = []

        for passage in passages:
            latest_run = (
                self.db.query(ExecutionRun)
                .filter(ExecutionRun.passage_id == passage.doc_id)
                .order_by(ExecutionRun.started_at.desc(), ExecutionRun.id.desc())
                .first()
            )
            summary = None
            if latest_run is not None:
                summary = (
                    self.db.query(ExecutionTraceSummary)
                    .filter(ExecutionTraceSummary.execution_run_id == latest_run.id)
                    .first()
                )

            hit = int(summary.prompt_cache_hit_tokens) if summary is not None else 0
            miss = int(summary.prompt_cache_miss_tokens) if summary is not None else 0
            ratio = hit / (hit + miss) if (hit + miss) > 0 else 0.0
            rows.append(
                PassageUsageOverviewRow(
                    doc_id=passage.doc_id,
                    title=passage.title,
                    workflow_status=passage.workflow_status,
                    llm_call_count=int(summary.llm_call_count) if summary is not None else 0,
                    total_tokens=int(summary.total_tokens) if summary is not None else 0,
                    prompt_cache_hit_tokens=hit,
                    prompt_cache_miss_tokens=miss,
                    cache_hit_ratio=ratio,
                    estimated_total_cost=(
                        float(summary.estimated_total_cost) if summary is not None else 0.0
                    ),
                    currency=summary.currency if summary is not None else "CNY",
                )
            )

        return rows

    def update_workflow_status(self, passage: Passage, status: str) -> Passage:
        """更新文章的最新 workflow 状态。"""

        passage.workflow_status = status
        self.db.add(passage)
        self.db.commit()
        self.db.refresh(passage)
        return passage

    # 纯文本类后缀走 fast path（直接 UTF-8 decode），无需 markitdown 依赖。
    _PLAIN_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})

    def extract_text_via_markitdown(self, file_name: str, file_bytes: bytes) -> tuple[str, str]:
        """统一把任意上传格式转成 .md 文本。

        - 纯文本后缀（.md/.markdown/.txt）走 fast path：直接 UTF-8 decode。
        - 二进制后缀（.docx/.pdf/.pptx/.xlsx 等）走 markitdown。
        title 由文件名 stem 给出，context 为转换后的 markdown 文本。
        """

        title = Path(file_name).stem
        suffix = Path(file_name).suffix.lower()

        if suffix not in MARKITDOWN_SUPPORTED_SUFFIXES:
            raise ValueError(f"不支持的文件后缀：{suffix}")

        if suffix in self._PLAIN_TEXT_SUFFIXES:
            try:
                context = file_bytes.decode("utf-8").strip()
            except UnicodeDecodeError:
                # 兜底：少量历史文献文件可能是 GBK / GB18030 编码。
                context = file_bytes.decode("gb18030", errors="replace").strip()
            return title, context

        # 走 markitdown：仅二进制格式需要它解析。
        from markitdown import MarkItDown  # 延迟导入，未装时也不影响 fast path

        md = MarkItDown(enable_plugins=False)
        result = md.convert_stream(io.BytesIO(file_bytes), file_extension=suffix)
        context = (result.text_content or "").strip()
        return title, context
