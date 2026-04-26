"""古籍文章服务。"""

import io
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.execution import ExecutionRun, ExecutionStepRun
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


class PassageService:
    """处理古籍上传、录入与任务查询。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_passage(self, user: User, title: str, context: str, source_type: str, file_name: str | None = None) -> Passage:
        """创建 Passage 数据。"""

        passage = Passage(
            title=title,
            context=context,
            source_type=source_type,
            file_name=file_name,
            created_by=user.id,
            workflow_status="pending",
        )
        self.db.add(passage)
        self.db.commit()
        self.db.refresh(passage)
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
