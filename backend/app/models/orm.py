"""ORM 三张表（Plan §4：users / interviews / messages）。

约定：
  * 字符集 utf8mb4（连接串 charset=utf8mb4），时间字段统一 DATETIME(3)；
  * 时间一律存本地时间（东八区），默认值取应用服务器当前时间；
  * 结构化大对象（D1–D5、报告等）存 JSON 列，字段契约见 models/schemas.py；
  * 建表走 Base.metadata.create_all（main.py lifespan），不用迁移工具。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# DateTime 加 MySQL 变体以启用毫秒精度（fsp=3）；非 MySQL 方言退回普通 DateTime
DateTimeMS = DateTime().with_variant(MYSQL_DATETIME(fsp=3), "mysql")

# 时间列的 DDL 默认值：原生 SQL / 其它客户端写入也有同款默认；
# 时区取 MySQL 服务器本地（东八区），与 ORM 侧 default=_now（应用服务器本地）一致
_CREATED_AT_DDL = text("CURRENT_TIMESTAMP(3)")
_UPDATED_AT_DDL = text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)")


def _now() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


class User(Base):
    """users：账号 + 个人资料 + 自定义模型配置 + 免费额度计数（§4.1）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 4~32 位字母/数字/下划线（schemas 层用正则校验，库层兜底长度）
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)  # bcrypt=60
    display_name: Mapped[str | None] = mapped_column(String(64))
    target_position: Mapped[str | None] = mapped_column(String(64))

    # 自定义模型配置（P5 功能，字段先建好）；key 只存 AES-256-GCM 密文+nonce
    llm_base_url: Mapped[str | None] = mapped_column(String(255))
    llm_model: Mapped[str | None] = mapped_column(String(128))
    llm_key_cipher: Mapped[bytes | None] = mapped_column(LargeBinary(255))
    llm_key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(16))

    # 当日免费额度（跨日重置：配额日期 != 今天 → 计数归零）
    quota_date: Mapped[date | None] = mapped_column()
    # default= 是 ORM 侧（INSERT 立即有值）；server_default= 落到 DDL（
    # 原生 SQL / 其它客户端插入也有同款默认值）——两处保持一致
    quota_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTimeMS, default=_now, server_default=_CREATED_AT_DDL, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTimeMS,
        default=_now,
        onupdate=_now,
        server_default=_UPDATED_AT_DDL,
        nullable=False,
    )

    interviews: Mapped[list["Interview"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_custom_llm(self) -> bool:
        """自定义模型配置齐活才算启用（三要素 + key 密文）。"""
        return bool(self.llm_base_url and self.llm_model and self.llm_key_cipher)


class Interview(Base):
    """interviews：一场面试一条（§4.2），状态流转见 Plan §6.3 状态机。"""

    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # preparing → awaiting_confirm → initializing → ready → queued/running
    # → finished / aborted
    status: Mapped[str] = mapped_column(
        String(20), default="preparing", server_default=text("'preparing'"), nullable=False
    )
    stage: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )  # 1~5

    # 五个后台任务状态：pending / running / done / partial / failed
    parse_status: Mapped[str] = mapped_column(
        String(10), default="pending", server_default=text("'pending'"), nullable=False
    )
    analysis_status: Mapped[str] = mapped_column(
        String(10), default="pending", server_default=text("'pending'"), nullable=False
    )
    research_status: Mapped[str] = mapped_column(
        String(10), default="pending", server_default=text("'pending'"), nullable=False
    )
    init_status: Mapped[str] = mapped_column(
        String(10), default="pending", server_default=text("'pending'"), nullable=False
    )
    report_status: Mapped[str] = mapped_column(
        String(10), default="pending", server_default=text("'pending'"), nullable=False
    )

    # uploads/{uuid}{ext}；30 天后删除文件并置空
    resume_path: Mapped[str | None] = mapped_column(String(255))
    repo_urls: Mapped[list | None] = mapped_column(JSON)  # 1~3 个公开仓库地址

    # D1~D5 结构化产物（契约见 schemas.py）
    profile: Mapped[dict | None] = mapped_column(JSON)  # D1 简历画像
    project_reports: Mapped[list | None] = mapped_column(JSON)  # D2 项目分析报告列表
    verification_items: Mapped[list | None] = mapped_column(JSON)  # D3 核验条目
    persona: Mapped[dict | None] = mapped_column(JSON)  # D4 面试官人设
    directions: Mapped[list | None] = mapped_column(JSON)  # D5 提问方向（含运行时字段）

    config: Mapped[dict | None] = mapped_column(JSON)  # {style, difficulty, position}

    report: Mapped[dict | None] = mapped_column(JSON)  # 评估报告
    total_score: Mapped[float | None] = mapped_column(Numeric(4, 1))  # 加权总分，1 位小数
    token_usage: Mapped[dict | None] = mapped_column(JSON)  # {input, output} 累计

    generating: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    uses_free_quota: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    warnings: Mapped[list | None] = mapped_column(JSON)  # 解析/核验过程的提示信息

    created_at: Mapped[datetime] = mapped_column(
        DateTimeMS, default=_now, server_default=_CREATED_AT_DDL, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTimeMS)
    ended_at: Mapped[datetime | None] = mapped_column(DateTimeMS)
    updated_at: Mapped[datetime] = mapped_column(
        DateTimeMS,
        default=_now,
        onupdate=_now,
        server_default=_UPDATED_AT_DDL,
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="interviews")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )


class Message(Base):
    """messages：面试内消息（§4.3），seq 场内从 1 递增。"""

    __tablename__ = "messages"
    __table_args__ = (
        # 候选人消息幂等键：同一场面试里重复 client_msg_id 直接拒绝/复用
        UniqueConstraint("interview_id", "client_msg_id", name="uq_message_client_msg_id"),
        Index("ix_messages_interview_seq", "interview_id", "seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    client_msg_id: Mapped[str | None] = mapped_column(String(64))

    role: Mapped[str] = mapped_column(String(12), nullable=False)  # interviewer / candidate
    stage: Mapped[int | None] = mapped_column(Integer)
    direction_id: Mapped[str | None] = mapped_column(String(16))  # 所属提问方向

    content: Mapped[str] = mapped_column(Text, nullable=False)  # 定稿文本（≤300 字，纯文本）
    original_content: Mapped[str | None] = mapped_column(Text)  # 幻觉修正前原文
    corrected: Mapped[str] = mapped_column(
        String(10), default="none", server_default=text("'none'"), nullable=False
    )
    # none / fixed / fallback

    is_followup: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    evidence_ids: Mapped[list | None] = mapped_column(JSON)  # C-xxx / F-xxx / V-xxx
    verify_result: Mapped[dict | None] = mapped_column(JSON)  # 面试中核实结论（V-xxx）

    answer_duration_s: Mapped[int | None] = mapped_column(Integer)  # 候选人作答用时
    response_ms: Mapped[int | None] = mapped_column(Integer)  # 面试官响应耗时

    created_at: Mapped[datetime] = mapped_column(
        DateTimeMS, default=_now, server_default=_CREATED_AT_DDL, nullable=False
    )

    interview: Mapped[Interview] = relationship(back_populates="messages")
