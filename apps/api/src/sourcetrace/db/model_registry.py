from sourcetrace.db.base import Base
from sourcetrace.modules.documents.models import Document, DocumentVersion
from sourcetrace.modules.knowledge_bases.models import KnowledgeBase

REGISTERED_MODELS = (KnowledgeBase, Document, DocumentVersion)
MODEL_METADATA = Base.metadata
