from sourcetrace.db.base import Base
from sourcetrace.modules.answers.models import AnswerRun, Citation
from sourcetrace.modules.conversations.models import Conversation, Question
from sourcetrace.modules.documents.models import Chunk, Document, DocumentVersion, IngestionRun
from sourcetrace.modules.knowledge_bases.models import KnowledgeBase

REGISTERED_MODELS = (
    AnswerRun,
    Citation,
    KnowledgeBase,
    Document,
    DocumentVersion,
    IngestionRun,
    Chunk,
    Conversation,
    Question,
)
MODEL_METADATA = Base.metadata
