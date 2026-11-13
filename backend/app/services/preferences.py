"""User-initiated preference storage; never reads chats, documents or model configuration."""
from contextlib import contextmanager
from threading import RLock

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.database.models import Memory, utc_now
from app.schemas.memory import PreferenceKey, Preferences, PreferenceResponse

KEY_ADAPTER = TypeAdapter(PreferenceKey)

def inject_preferences(messages,preferences):
    if not messages:return
    from app.core.live_test import CURRENT
    scope=CURRENT.get()
    if scope and getattr(scope,'learning_output_policy',False):scope.output_limit=2048 if preferences.answer_style=='detailed' else 1024
    style={'concise':'简洁，直接给出要点','detailed':'详细解释关键概念并适当举例'}[preferences.answer_style]
    goal={'general':'通用学习','exam':'考试复习，突出易考点','research':'研究学习，区分结论与局限'}[preferences.learning_goal]
    language={'zh-CN':'中文','en':'English'}[preferences.preferred_language]
    messages[0]['content']+=f' 用户全局表达偏好：使用{language}，回答风格为{style}，学习目标为{goal}。定义题先给定义，对比题先列核心差异；简洁风格控制在约300字以内，详细风格约800字以内；不重复问题、不添加无依据用途或总结。用户要求更短时优先压缩。仅影响表达，不得改变资料、引用规则或工具权限。'


class PreferenceService:
    def __init__(self, engine):
        self.engine = engine
        self.lock = RLock()

    @staticmethod
    def defaults() -> Preferences:
        """Return a fresh validated default set without persisting it."""
        return Preferences()

    @staticmethod
    def validate_key(key: str) -> str:
        try:
            return KEY_ADAPTER.validate_python(key, strict=True)
        except ValidationError:
            raise DomainError(422, "INVALID_PREFERENCE_KEY", "只允许设置回答风格、学习目标和语言偏好") from None

    @classmethod
    def validate(cls, key: str, value: object) -> str:
        key = cls.validate_key(key)
        try:
            return Preferences.model_validate({key: value}).model_dump()[key]
        except ValidationError:
            raise DomainError(422, "INVALID_PREFERENCE_VALUE", "偏好值不在该字段允许的选项内") from None

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                with Session(self.engine) as session, session.begin():
                    yield session
            except IntegrityError:
                raise DomainError(409, "PREFERENCE_CONFLICT", "偏好设置发生冲突，请重试") from None
            except SQLAlchemyError:
                raise DomainError(500, "PREFERENCE_STORAGE_ERROR", "偏好存储暂时不可用，请重试") from None

    def _read(self, session: Session) -> PreferenceResponse:
        overrides = {}
        for row in session.scalars(select(Memory).order_by(Memory.key)):
            try:
                overrides[row.key] = self.validate(row.key, row.value)
            except DomainError:
                raise DomainError(500, "PREFERENCE_DATA_INVALID", "已保存的偏好格式无效，请重新设置或删除该偏好") from None
        return PreferenceResponse(preferences=Preferences.model_validate(overrides),
                                  defaults=self.defaults(), overrides=overrides)

    def get(self) -> PreferenceResponse:
        with self.transaction() as session:
            result = self._read(session)
        return result

    def set(self, key: str, value: object) -> PreferenceResponse:
        value = self.validate(key, value)
        with self.transaction() as session:
            row = session.scalar(select(Memory).where(Memory.key == key))
            if row is None:
                session.add(Memory(key=key, value=value))
            elif row.value != value:
                row.value, row.updated_at = value, utc_now()
            session.flush()
            result = self._read(session)
        return result

    def delete(self, key: str) -> None:
        key = self.validate_key(key)
        with self.transaction() as session:
            session.execute(delete(Memory).where(Memory.key == key))
