"""古籍文章服务。"""

from pathlib import Path

from docx import Document
from sqlalchemy.orm import Session

from app.models.execution import ExecutionRun, ExecutionStepRun
from app.models.passage import Passage
from app.models.user import User


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

    def extract_text_from_md(self, file_name: str, file_bytes: bytes) -> tuple[str, str]:
        """从 md 文件提取标题与正文。"""

        title = Path(file_name).stem
        context = file_bytes.decode("utf-8")
        return title, context

    def extract_text_from_docx(self, file_name: str, file_bytes: bytes) -> tuple[str, str]:
        """从 docx 文件提取标题与正文。"""

        import io

        title = Path(file_name).stem
        document = Document(io.BytesIO(file_bytes))
        context = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
        return title, context
